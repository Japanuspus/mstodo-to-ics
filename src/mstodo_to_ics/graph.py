"""Strictly read-only Microsoft Graph retrieval for To Do lists and tasks."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Protocol, Self, cast
from urllib.parse import quote, urlsplit

import httpx

from .export import RawExportData, RawListExport

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"

Sleeper = Callable[[float], None]
Clock = Callable[[], datetime]


class AccessTokenProvider(Protocol):
    """Supplies Graph bearer tokens and supports one forced refresh."""

    def get_token(self, *, force_refresh: bool = False) -> str:
        """Return an access token, refreshing it when explicitly requested."""
        ...


class GraphError(RuntimeError):
    """Base class for Graph retrieval failures."""


class GraphSecurityError(GraphError):
    """Raised when Graph supplies an unsafe continuation URL."""


class GraphProtocolError(GraphError):
    """Raised when a successful Graph response has an invalid shape."""


class GraphPaginationError(GraphProtocolError):
    """Raised when pagination cannot make safe forward progress."""


class GraphTransportError(GraphError):
    """Raised when HTTP transport retries are exhausted."""


class GraphApiError(GraphError):
    """A non-success Graph response with structured diagnostic fields."""

    def __init__(
        self,
        *,
        status_code: int,
        url: str,
        code: str | None,
        detail: str,
        request_id: str | None,
    ) -> None:
        self.status_code = status_code
        self.url = url
        self.code = code
        self.detail = detail
        self.request_id = request_id
        label = f"Graph {status_code}"
        if code:
            label += f" {code}"
        message = f"{label} on {url}: {detail}"
        if request_id:
            message += f" (request-id: {request_id})"
        super().__init__(message)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class GraphClient:
    """Minimal Graph v1.0 reader with complete, validated pagination."""

    def __init__(
        self,
        token_provider: AccessTokenProvider,
        *,
        http_client: httpx.Client | None = None,
        max_retries: int = 3,
        sleeper: Sleeper = time.sleep,
        clock: Clock = _utc_now,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries cannot be negative")
        self._token_provider = token_provider
        self._client = http_client or httpx.Client(timeout=30.0, follow_redirects=False)
        self._owns_client = http_client is None
        self._max_retries = max_retries
        self._sleep = sleeper
        self._clock = clock

    def close(self) -> None:
        """Close an internally-created HTTP client."""
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def fetch_export_data(self) -> RawExportData:
        """Retrieve every list and every task, including completed tasks."""
        raw_lists, list_pages = self._get_collection("/me/todo/lists")
        sources: list[RawListExport] = []
        for raw_list in raw_lists:
            list_id = raw_list.get("id")
            if not isinstance(list_id, str):
                raise GraphProtocolError("a list entity is missing a string id")
            encoded_list_id = quote(list_id, safe="")
            raw_tasks, task_pages = self._get_collection(f"/me/todo/lists/{encoded_list_id}/tasks")
            sources.append(
                RawListExport.from_raw(
                    raw_list,
                    raw_tasks,
                    raw_task_pages=task_pages,
                )
            )
        return RawExportData(sources=tuple(sources), raw_list_pages=list_pages)

    def _get_collection(
        self, initial_path: str
    ) -> tuple[tuple[Mapping[str, Any], ...], tuple[Mapping[str, Any], ...]]:
        next_url: str | None = self._absolute_url(initial_path)
        seen: set[str] = set()
        items: list[Mapping[str, Any]] = []
        pages: list[Mapping[str, Any]] = []

        while next_url is not None:
            if next_url in seen:
                raise GraphPaginationError(f"Graph pagination loop detected at {next_url}")
            seen.add(next_url)
            page = self._get_json(next_url)
            value = page.get("value")
            if not isinstance(value, list):
                raise GraphProtocolError(f"Graph collection at {next_url} has no array value")
            for index, item in enumerate(value):
                if not isinstance(item, Mapping):
                    raise GraphProtocolError(
                        f"Graph collection item {index} at {next_url} is not an object"
                    )
                items.append(cast(Mapping[str, Any], item))
            pages.append(page)

            continuation = page.get("@odata.nextLink")
            if continuation is None:
                next_url = None
            elif isinstance(continuation, str) and continuation:
                next_url = self._safe_graph_url(continuation)
            else:
                raise GraphProtocolError(
                    f"Graph collection at {next_url} has an invalid @odata.nextLink"
                )

        return tuple(items), tuple(pages)

    def _absolute_url(self, path: str) -> str:
        if not path.startswith("/"):
            raise ValueError("Graph paths must start with a slash")
        return self._safe_graph_url(f"{GRAPH_BASE_URL}{path}")

    def _safe_graph_url(self, url: str) -> str:
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError as error:
            raise GraphSecurityError(f"invalid Graph URL: {url!r}") from error
        if (
            parsed.scheme.casefold() != "https"
            or parsed.hostname is None
            or parsed.hostname.casefold() != "graph.microsoft.com"
            or port not in (None, 443)
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or not parsed.path.startswith("/v1.0/")
        ):
            raise GraphSecurityError(f"refusing unsafe Graph URL: {url!r}")
        return url

    def _get_json(self, url: str) -> Mapping[str, Any]:
        retries = 0
        refreshed = False
        force_refresh = False
        while True:
            token = self._token_provider.get_token(force_refresh=force_refresh)
            force_refresh = False
            if not token:
                raise GraphError("access token provider returned an empty token")
            try:
                response = self._client.get(
                    url,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/json",
                    },
                    follow_redirects=False,
                )
            except httpx.TransportError as error:
                if retries >= self._max_retries:
                    raise GraphTransportError(
                        f"Graph transport failed after {retries + 1} attempt(s): {error}"
                    ) from error
                self._sleep(0.5 * (2**retries))
                retries += 1
                continue

            if response.status_code == 401 and not refreshed:
                refreshed = True
                force_refresh = True
                continue

            retryable = response.status_code == 429 or 500 <= response.status_code < 600
            if retryable and retries < self._max_retries:
                self._sleep(self._retry_delay(response, retries))
                retries += 1
                continue

            if not response.is_success:
                raise self._api_error(response)

            try:
                payload = response.json()
            except ValueError as error:
                raise GraphProtocolError(f"Graph returned invalid JSON from {url}") from error
            if not isinstance(payload, Mapping):
                raise GraphProtocolError(f"Graph returned a non-object JSON value from {url}")
            return cast(Mapping[str, Any], payload)

    def _retry_delay(self, response: httpx.Response, retry_number: int) -> float:
        value = response.headers.get("Retry-After")
        if value:
            try:
                seconds = float(value)
            except ValueError:
                try:
                    retry_at = parsedate_to_datetime(value)
                    if retry_at.tzinfo is None or retry_at.utcoffset() is None:
                        retry_at = retry_at.replace(tzinfo=UTC)
                    return max(0.0, (retry_at.astimezone(UTC) - self._clock()).total_seconds())
                except (TypeError, ValueError, OverflowError):
                    pass
            else:
                return max(0.0, seconds)
        return 0.5 * (2.0**retry_number)

    def _api_error(self, response: httpx.Response) -> GraphApiError:
        code: str | None = None
        detail = response.text or response.reason_phrase or "request failed"
        request_id = response.headers.get("request-id")
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if isinstance(payload, Mapping):
            error = payload.get("error")
            if isinstance(error, Mapping):
                raw_code = error.get("code")
                raw_message = error.get("message")
                if isinstance(raw_code, str):
                    code = raw_code
                if isinstance(raw_message, str):
                    detail = raw_message
                inner = error.get("innerError")
                if request_id is None and isinstance(inner, Mapping):
                    raw_request_id = inner.get("request-id")
                    if isinstance(raw_request_id, str):
                        request_id = raw_request_id
        return GraphApiError(
            status_code=response.status_code,
            url=str(response.request.url),
            code=code,
            detail=detail,
            request_id=request_id,
        )
