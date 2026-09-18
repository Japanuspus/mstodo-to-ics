from __future__ import annotations

import json
import stat
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from mstodo_to_ics.auth import (
    AUTHORITY,
    PRIVATE_TOKEN_CACHE_FILENAME,
    PRIVATE_TOKEN_CACHE_LOCK_FILENAME,
    SCOPES,
    AuthenticationError,
    MsalTokenProvider,
    TokenCacheError,
)


class FakeCache:
    def __init__(self) -> None:
        self.has_state_changed = False
        self.state = "{}"
        self.loaded_states: list[str | None] = []

    def deserialize(self, state: str | None) -> None:
        if state is not None:
            json.loads(state)
        self.loaded_states.append(state)
        self.state = state or "{}"
        self.has_state_changed = False

    def serialize(self) -> str:
        self.has_state_changed = False
        return self.state


class FakeApplication:
    def __init__(self, cache: FakeCache) -> None:
        self.cache = cache
        self.accounts: list[dict[str, Any]] = []
        self.silent_result: Mapping[str, Any] | None = None
        self.flow: Mapping[str, Any] = {
            "user_code": "ABCD-EFGH",
            "message": "Visit the Microsoft sign-in page and enter ABCD-EFGH.",
        }
        self.device_result: Mapping[str, Any] = {"access_token": "device-token"}
        self.silent_calls: list[tuple[tuple[str, ...], Mapping[str, Any], bool]] = []
        self.device_flow_scopes: list[tuple[str, ...]] = []
        self.device_acquisition_count = 0

    def get_accounts(self, username: str | None = None) -> list[dict[str, Any]]:
        assert username is None
        return self.accounts

    def acquire_token_silent(
        self,
        scopes: Sequence[str],
        account: Mapping[str, Any],
        *,
        force_refresh: bool = False,
    ) -> Mapping[str, Any] | None:
        self.silent_calls.append((tuple(scopes), account, force_refresh))
        return self.silent_result

    def initiate_device_flow(self, scopes: Sequence[str]) -> Mapping[str, Any]:
        self.device_flow_scopes.append(tuple(scopes))
        return self.flow

    def acquire_token_by_device_flow(
        self,
        flow: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        assert flow is self.flow
        self.device_acquisition_count += 1
        self.cache.state = '{"private": "refresh-token"}'
        self.cache.has_state_changed = True
        return self.device_result


def _provider(
    cache: FakeCache,
    application: FakeApplication,
    *,
    persist_cache: bool = False,
    allow_device_code: bool = True,
    messages: list[str] | None = None,
    construction: list[tuple[str, str]] | None = None,
) -> MsalTokenProvider:
    def application_factory(
        client_id: str,
        authority: str,
        supplied_cache: Any,
    ) -> FakeApplication:
        assert supplied_cache is cache
        if construction is not None:
            construction.append((client_id, authority))
        return application

    return MsalTokenProvider(
        "public-client-id",
        persist_cache=persist_cache,
        allow_device_code=allow_device_code,
        device_code_callback=(messages.append if messages is not None else lambda _: None),
        application_factory=application_factory,
        cache_factory=lambda: cache,
    )


def test_memory_only_device_flow_creates_no_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    cache = FakeCache()
    application = FakeApplication(cache)
    messages: list[str] = []
    construction: list[tuple[str, str]] = []
    provider = _provider(
        cache,
        application,
        messages=messages,
        construction=construction,
    )

    token = provider.get_token()

    assert token == "device-token"
    assert provider.cache_path is None
    assert messages == ["Visit the Microsoft sign-in page and enter ABCD-EFGH."]
    assert application.device_flow_scopes == [SCOPES]
    assert construction == [("public-client-id", AUTHORITY)]
    assert not tuple(tmp_path.iterdir())


def test_persistent_cache_is_private_atomic_and_clearly_named(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    cache = FakeCache()
    application = FakeApplication(cache)
    provider = _provider(cache, application, persist_cache=True)

    assert provider.get_token() == "device-token"

    cache_path = tmp_path / PRIVATE_TOKEN_CACHE_FILENAME
    lock_path = tmp_path / PRIVATE_TOKEN_CACHE_LOCK_FILENAME
    assert provider.cache_path == cache_path
    assert cache_path.read_text(encoding="utf-8") == '{"private": "refresh-token"}'
    assert stat.S_IMODE(cache_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600
    assert not tuple(tmp_path.glob(f"{PRIVATE_TOKEN_CACHE_FILENAME}.tmp-*"))


def test_persistent_cache_loads_before_silent_acquisition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    cache_path = tmp_path / PRIVATE_TOKEN_CACHE_FILENAME
    initial_state = '{"cached": true}'
    cache_path.write_text(initial_state, encoding="utf-8")
    cache_path.chmod(0o600)
    cache = FakeCache()
    application = FakeApplication(cache)
    account = {"home_account_id": "account-1", "username": "person@example.test"}
    application.accounts = [account]
    application.silent_result = {"access_token": "cached-token"}
    provider = _provider(cache, application, persist_cache=True)

    assert provider.get_token() == "cached-token"

    assert cache.loaded_states == [initial_state]
    assert application.silent_calls == [(SCOPES, account, False)]
    assert application.device_acquisition_count == 0
    assert cache_path.read_text(encoding="utf-8") == initial_state


def test_force_refresh_is_forwarded_to_msal() -> None:
    cache = FakeCache()
    application = FakeApplication(cache)
    account = {"home_account_id": "account-1"}
    application.accounts = [account]
    application.silent_result = {"access_token": "fresh-token"}
    provider = _provider(cache, application)

    assert provider.get_token(force_refresh=True) == "fresh-token"
    assert application.silent_calls == [(SCOPES, account, True)]


def test_silent_cache_miss_falls_back_to_device_flow() -> None:
    cache = FakeCache()
    application = FakeApplication(cache)
    application.accounts = [{"home_account_id": "account-1"}]
    application.silent_result = None
    provider = _provider(cache, application)

    assert provider.get_token() == "device-token"
    assert application.device_acquisition_count == 1


def test_noninteractive_cache_miss_requires_auth_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    cache = FakeCache()
    application = FakeApplication(cache)
    provider = _provider(cache, application, persist_cache=True, allow_device_code=False)

    with pytest.raises(AuthenticationError, match="run 'mstodo-to-ics auth' first"):
        provider.get_token()
    assert application.device_acquisition_count == 0


def test_multiple_cached_accounts_are_not_selected_implicitly() -> None:
    cache = FakeCache()
    application = FakeApplication(cache)
    application.accounts = [
        {"home_account_id": "account-1"},
        {"home_account_id": "account-2"},
    ]
    provider = _provider(cache, application)

    with pytest.raises(AuthenticationError, match="multiple Microsoft accounts"):
        provider.get_token()
    assert application.silent_calls == []
    assert application.device_acquisition_count == 0


def test_device_flow_initialization_error_is_structured_and_does_not_leak_token() -> None:
    cache = FakeCache()
    application = FakeApplication(cache)
    application.flow = {
        "error": "authorization_pending",
        "error_description": "Device authorization could not start",
        "correlation_id": "correlation-1",
        "access_token": "must-not-leak",
    }
    provider = _provider(cache, application)

    with pytest.raises(AuthenticationError) as exc_info:
        provider.get_token()
    error = exc_info.value
    assert error.code == "authorization_pending"
    assert error.correlation_id == "correlation-1"
    assert "Device authorization could not start" in str(error)
    assert "must-not-leak" not in str(error)


def test_device_flow_completion_error_is_structured() -> None:
    cache = FakeCache()
    application = FakeApplication(cache)
    application.device_result = {
        "error": "authorization_declined",
        "error_description": "The user declined authentication",
        "correlation_id": "correlation-2",
    }
    provider = _provider(cache, application)

    with pytest.raises(AuthenticationError) as exc_info:
        provider.get_token()
    error = exc_info.value
    assert error.code == "authorization_declined"
    assert error.correlation_id == "correlation-2"
    assert "The user declined authentication" in str(error)


def test_rejects_persistent_cache_with_broad_permissions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    cache_path = tmp_path / PRIVATE_TOKEN_CACHE_FILENAME
    cache_path.write_text("{}", encoding="utf-8")
    cache_path.chmod(0o644)
    cache = FakeCache()
    provider = _provider(cache, FakeApplication(cache), persist_cache=True)

    with pytest.raises(TokenCacheError, match="permissions are too broad"):
        provider.get_token()


def test_rejects_symlinked_persistent_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "not-the-cache.json"
    target.write_text("{}", encoding="utf-8")
    target.chmod(0o600)
    (tmp_path / PRIVATE_TOKEN_CACHE_FILENAME).symlink_to(target)
    cache = FakeCache()
    provider = _provider(cache, FakeApplication(cache), persist_cache=True)

    with pytest.raises(TokenCacheError, match="not a regular file"):
        provider.get_token()


def test_rejects_corrupt_persistent_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    cache_path = tmp_path / PRIVATE_TOKEN_CACHE_FILENAME
    cache_path.write_text("not-json", encoding="utf-8")
    cache_path.chmod(0o600)
    cache = FakeCache()
    provider = _provider(cache, FakeApplication(cache), persist_cache=True)

    with pytest.raises(TokenCacheError, match="could not read private token cache"):
        provider.get_token()


def test_rejects_empty_client_id() -> None:
    with pytest.raises(ValueError, match="client_id cannot be empty"):
        MsalTokenProvider("   ")
