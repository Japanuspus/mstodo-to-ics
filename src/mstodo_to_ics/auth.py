"""Microsoft device-code authentication with optional local cache persistence."""

from __future__ import annotations

import os
import stat
import tempfile
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager, nullcontext, suppress
from pathlib import Path
from typing import Any, Protocol, cast

import msal  # type: ignore[import-untyped]
from filelock import FileLock, Timeout

AUTHORITY = "https://login.microsoftonline.com/consumers"
SCOPES = ("Tasks.Read",)
PRIVATE_TOKEN_CACHE_FILENAME = ".mstodo-to-ics-private-token-cache.json"
PRIVATE_TOKEN_CACHE_LOCK_FILENAME = ".mstodo-to-ics-private-token-cache.lock"

DeviceCodeCallback = Callable[[str], None]


class AuthenticationError(RuntimeError):
    """Raised when Microsoft authentication cannot produce an access token."""

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        correlation_id: str | None = None,
    ) -> None:
        self.code = code
        self.correlation_id = correlation_id
        detail = message
        if code:
            detail += f" [{code}]"
        if correlation_id:
            detail += f" (correlation-id: {correlation_id})"
        super().__init__(detail)


class TokenCacheError(AuthenticationError):
    """Raised when the opt-in persistent token cache cannot be used safely."""


class _SerializableTokenCache(Protocol):
    has_state_changed: bool

    def deserialize(self, state: str | None) -> None: ...

    def serialize(self) -> str: ...


class _PublicClientApplication(Protocol):
    def get_accounts(self, username: str | None = None) -> list[dict[str, Any]]: ...

    def acquire_token_silent(
        self,
        scopes: Sequence[str],
        account: Mapping[str, Any],
        *,
        force_refresh: bool = False,
    ) -> Mapping[str, Any] | None: ...

    def initiate_device_flow(self, scopes: Sequence[str]) -> Mapping[str, Any]: ...

    def acquire_token_by_device_flow(self, flow: Mapping[str, Any]) -> Mapping[str, Any]: ...


CacheFactory = Callable[[], _SerializableTokenCache]
ApplicationFactory = Callable[
    [str, str, _SerializableTokenCache],
    _PublicClientApplication,
]


def _new_cache() -> _SerializableTokenCache:
    return cast(_SerializableTokenCache, msal.SerializableTokenCache())


def _new_application(
    client_id: str,
    authority: str,
    cache: _SerializableTokenCache,
) -> _PublicClientApplication:
    return cast(
        _PublicClientApplication,
        msal.PublicClientApplication(
            client_id,
            authority=authority,
            token_cache=cache,
        ),
    )


class MsalTokenProvider:
    """Supply delegated Graph tokens using MSAL's public-client device flow.

    Persistence is deliberately opt-in. When enabled, the unencrypted MSAL cache
    is stored in the current working directory with owner-only permissions.
    """

    def __init__(
        self,
        client_id: str,
        *,
        persist_cache: bool = False,
        device_code_callback: DeviceCodeCallback = print,
        application_factory: ApplicationFactory = _new_application,
        cache_factory: CacheFactory = _new_cache,
    ) -> None:
        if not client_id.strip():
            raise ValueError("client_id cannot be empty")
        self._persist_cache = persist_cache
        self._device_code_callback = device_code_callback
        self._cache_path = Path.cwd() / PRIVATE_TOKEN_CACHE_FILENAME
        self._lock_path = Path.cwd() / PRIVATE_TOKEN_CACHE_LOCK_FILENAME
        self._cache = cache_factory()
        self._application = application_factory(client_id, AUTHORITY, self._cache)
        self._file_lock = FileLock(
            self._lock_path,
            timeout=10,
            mode=0o600,
            preserve_lock_file=True,
        )

    @property
    def cache_path(self) -> Path | None:
        """Return the persistent cache path, or ``None`` in memory-only mode."""
        return self._cache_path if self._persist_cache else None

    def get_token(self, *, force_refresh: bool = False) -> str:
        """Return a Tasks.Read token, using device code only when silence fails."""
        try:
            with self._cache_guard():
                if self._persist_cache:
                    self._load_cache()
                token = self._acquire_token(force_refresh=force_refresh)
                if self._persist_cache and self._cache.has_state_changed:
                    self._save_cache()
                return token
        except Timeout as error:
            raise TokenCacheError(
                f"timed out waiting for private token cache lock: {self._lock_path}"
            ) from error

    def _cache_guard(self) -> AbstractContextManager[object]:
        if self._persist_cache:
            return self._file_lock
        return nullcontext()

    def _acquire_token(self, *, force_refresh: bool) -> str:
        try:
            accounts = self._application.get_accounts()
        except Exception as error:
            raise AuthenticationError("could not inspect the Microsoft token cache") from error

        if len(accounts) > 1:
            raise AuthenticationError(
                "multiple Microsoft accounts are present in the token cache; "
                "account selection is required"
            )

        if accounts:
            try:
                result = self._application.acquire_token_silent(
                    list(SCOPES),
                    accounts[0],
                    force_refresh=force_refresh,
                )
            except Exception as error:
                raise AuthenticationError("silent Microsoft authentication failed") from error
            token = self._access_token(result)
            if token is not None:
                return token

        return self._acquire_by_device_code()

    def _acquire_by_device_code(self) -> str:
        try:
            flow = self._application.initiate_device_flow(list(SCOPES))
        except Exception as error:
            raise AuthenticationError("could not start Microsoft device-code login") from error

        user_code = flow.get("user_code")
        message = flow.get("message")
        if not isinstance(user_code, str) or not user_code:
            raise self._result_error("Microsoft did not return a usable device code", flow)
        if not isinstance(message, str) or not message:
            raise AuthenticationError("Microsoft device-code response has no login instructions")

        self._device_code_callback(message)
        try:
            result = self._application.acquire_token_by_device_flow(flow)
        except Exception as error:
            raise AuthenticationError("Microsoft device-code login failed") from error

        token = self._access_token(result)
        if token is None:
            raise self._result_error("Microsoft device-code login failed", result)
        return token

    def _access_token(self, result: Mapping[str, Any] | None) -> str | None:
        if result is None:
            return None
        token = result.get("access_token")
        return token if isinstance(token, str) and token else None

    def _result_error(
        self,
        message: str,
        result: Mapping[str, Any],
    ) -> AuthenticationError:
        code = result.get("error")
        description = result.get("error_description")
        correlation_id = result.get("correlation_id")
        safe_message = description if isinstance(description, str) and description else message
        return AuthenticationError(
            safe_message,
            code=code if isinstance(code, str) else None,
            correlation_id=(correlation_id if isinstance(correlation_id, str) else None),
        )

    def _load_cache(self) -> None:
        if not self._cache_path.exists() and not self._cache_path.is_symlink():
            return
        self._validate_cache_file()
        try:
            state = self._cache_path.read_text(encoding="utf-8")
            self._cache.deserialize(state)
        except (OSError, ValueError) as error:
            raise TokenCacheError(
                f"could not read private token cache: {self._cache_path}"
            ) from error

    def _validate_cache_file(self) -> None:
        try:
            metadata = self._cache_path.lstat()
        except OSError as error:
            raise TokenCacheError(
                f"could not inspect private token cache: {self._cache_path}"
            ) from error
        if not stat.S_ISREG(metadata.st_mode) or self._cache_path.is_symlink():
            raise TokenCacheError(f"private token cache is not a regular file: {self._cache_path}")
        if os.name != "nt" and stat.S_IMODE(metadata.st_mode) & 0o077:
            raise TokenCacheError(
                f"private token cache permissions are too broad; expected 0600: {self._cache_path}"
            )
        getuid = getattr(os, "getuid", None)
        if getuid is not None and metadata.st_uid != getuid():
            raise TokenCacheError(
                f"private token cache is owned by another user: {self._cache_path}"
            )

    def _save_cache(self) -> None:
        descriptor: int | None = None
        temporary_path: Path | None = None
        try:
            state = self._cache.serialize()
            descriptor, raw_path = tempfile.mkstemp(
                prefix=f"{PRIVATE_TOKEN_CACHE_FILENAME}.tmp-",
                dir=self._cache_path.parent,
                text=True,
            )
            temporary_path = Path(raw_path)
            if hasattr(os, "fchmod"):
                os.fchmod(descriptor, 0o600)
            handle = os.fdopen(descriptor, "w", encoding="utf-8", newline="")
            descriptor = None
            with handle:
                handle.write(state)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self._cache_path)
            temporary_path = None
        except (OSError, TypeError, ValueError) as error:
            raise TokenCacheError(
                f"could not write private token cache: {self._cache_path}"
            ) from error
        finally:
            if descriptor is not None:
                with suppress(OSError):
                    os.close(descriptor)
            if temporary_path is not None:
                with suppress(FileNotFoundError):
                    temporary_path.unlink()
