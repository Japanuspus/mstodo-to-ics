from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import format_datetime
from typing import Any

import httpx
import pytest

from mstodo_to_ics.export import plan_export
from mstodo_to_ics.graph import (
    GraphApiError,
    GraphClient,
    GraphPaginationError,
    GraphProtocolError,
    GraphSecurityError,
    GraphTransportError,
)

FIXED_NOW = datetime(2026, 9, 18, 12, 30, tzinfo=UTC)


class StubTokenProvider:
    def __init__(self) -> None:
        self.calls: list[bool] = []

    def get_token(self, *, force_refresh: bool = False) -> str:
        self.calls.append(force_refresh)
        return "fresh-token" if force_refresh else "cached-token"


def _http_client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _task(task_id: str, *, status: str = "notStarted") -> dict[str, Any]:
    completed = None
    if status == "completed":
        completed = {"dateTime": "2026-09-18T12:00:00.0000000", "timeZone": "UTC"}
    return {
        "id": task_id,
        "title": f"Task {task_id}",
        "body": {"content": "Body", "contentType": "text"},
        "status": status,
        "importance": "normal",
        "isReminderOn": False,
        "createdDateTime": "2026-09-17T09:00:00Z",
        "lastModifiedDateTime": "2026-09-18T12:00:00Z",
        "completedDateTime": completed,
        "dueDateTime": None,
        "startDateTime": None,
        "categories": [],
        "hasAttachments": False,
    }


def test_fetches_all_pages_preserves_envelopes_and_plans_archive() -> None:
    provider = StubTokenProvider()
    requests: list[httpx.Request] = []
    encoded_list_id = "list%2Fa%3F%3D%C3%A6"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET"
        assert request.headers["Authorization"] == "Bearer cached-token"
        assert "$filter" not in request.url.params
        assert "$select" not in request.url.params

        raw_path = request.url.raw_path.decode("ascii")
        if raw_path == "/v1.0/me/todo/lists":
            return httpx.Response(
                200,
                json={
                    "value": [{"id": "list/a?=æ", "displayName": "Special"}],
                    "@odata.context": "lists-context",
                    "@odata.nextLink": ("https://graph.microsoft.com/v1.0/me/todo/lists?page=2"),
                },
            )
        if raw_path == "/v1.0/me/todo/lists?page=2":
            return httpx.Response(
                200,
                json={"value": [{"id": "L2", "displayName": "Second"}]},
            )
        if raw_path == f"/v1.0/me/todo/lists/{encoded_list_id}/tasks":
            first_task = _task("T1")
            first_task["futureGraphField"] = {"preserve": True}
            return httpx.Response(
                200,
                json={
                    "value": [first_task],
                    "@odata.nextLink": (
                        "https://graph.microsoft.com/v1.0/me/todo/lists/"
                        f"{encoded_list_id}/tasks?$skiptoken=A%2FB%2B%3D"
                    ),
                    "futurePageField": "preserve this too",
                },
            )
        if raw_path == (f"/v1.0/me/todo/lists/{encoded_list_id}/tasks?$skiptoken=A%2FB%2B%3D"):
            return httpx.Response(200, json={"value": [_task("T2", status="completed")]})
        if raw_path == f"/v1.0/me/todo/lists/{encoded_list_id}/tasks/T1/checklistItems":
            return httpx.Response(
                200,
                json={
                    "value": [{"id": "C1", "displayName": "Første punkt", "isChecked": False}],
                    "@odata.nextLink": (
                        "https://graph.microsoft.com/v1.0/me/todo/lists/"
                        f"{encoded_list_id}/tasks/T1/checklistItems?$skiptoken=check%2F2"
                    ),
                    "futureChecklistPageField": "preserve checklist page",
                },
            )
        if raw_path == (
            f"/v1.0/me/todo/lists/{encoded_list_id}/tasks/T1/checklistItems?$skiptoken=check%2F2"
        ):
            return httpx.Response(
                200,
                json={"value": [{"id": "C2", "displayName": "Andet punkt", "isChecked": True}]},
            )
        if raw_path == f"/v1.0/me/todo/lists/{encoded_list_id}/tasks/T2/checklistItems":
            return httpx.Response(200, json={"value": []})
        if raw_path == "/v1.0/me/todo/lists/L2/tasks":
            return httpx.Response(200, json={"value": []})
        raise AssertionError(f"unexpected request: {request.url}")

    client = GraphClient(provider, http_client=_http_client(handler))
    export_data = client.fetch_export_data()

    assert [source.raw_list["id"] for source in export_data.sources] == ["list/a?=æ", "L2"]
    assert [task["id"] for task in export_data.sources[0].raw_tasks] == ["T1", "T2"]
    assert export_data.sources[0].raw_tasks[0]["futureGraphField"] == {"preserve": True}
    assert len(export_data.raw_list_pages) == 2
    assert len(export_data.sources[0].raw_task_pages) == 2
    assert export_data.sources[0].raw_task_pages[0]["futurePageField"] == ("preserve this too")
    assert export_data.sources[0].checklists_collected is True
    assert [item["id"] for item in export_data.sources[0].raw_checklists[0].raw_items] == [
        "C1",
        "C2",
    ]
    assert (
        export_data.sources[0].raw_checklists[0].raw_pages[0]["futureChecklistPageField"]
        == "preserve checklist page"
    )
    assert provider.calls == [False] * 8

    plan = plan_export(export_data, exported_at=FIXED_NOW)
    planned_paths = {path.as_posix() for path in plan.relative_paths}
    assert "raw/pages/lists/0001.json" in planned_paths
    assert "raw/pages/lists/0002.json" in planned_paths
    task_page_paths = [path for path in planned_paths if path.startswith("raw/pages/tasks/")]
    assert len(task_page_paths) == 3
    checklist_page_paths = [
        path for path in planned_paths if path.startswith("raw/pages/checklists/")
    ]
    assert len(checklist_page_paths) == 3
    manifest = plan.manifest
    assert manifest["raw_list_page_files"] == [
        "raw/pages/lists/0001.json",
        "raw/pages/lists/0002.json",
    ]
    assert len(manifest["lists"][0]["raw_task_page_files"]) == 2
    assert len(manifest["lists"][1]["raw_task_page_files"]) == 1
    assert all(request.method == "GET" for request in requests)


@pytest.mark.parametrize(
    "next_link",
    [
        "http://graph.microsoft.com/v1.0/me/todo/lists?page=2",
        "https://evil.example/v1.0/me/todo/lists?page=2",
        "https://user:password@graph.microsoft.com/v1.0/me/todo/lists?page=2",
        "https://graph.microsoft.com/beta/me/todo/lists?page=2",
    ],
)
def test_rejects_unsafe_next_links(next_link: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"value": [], "@odata.nextLink": next_link})

    client = GraphClient(StubTokenProvider(), http_client=_http_client(handler))

    with pytest.raises(GraphSecurityError):
        client.fetch_export_data()


def test_detects_pagination_loop_before_repeating_request() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={
                "value": [],
                "@odata.nextLink": "https://graph.microsoft.com/v1.0/me/todo/lists",
            },
        )

    client = GraphClient(StubTokenProvider(), http_client=_http_client(handler))

    with pytest.raises(GraphPaginationError):
        client.fetch_export_data()
    assert calls == 1


def test_does_not_forward_bearer_token_through_redirect() -> None:
    requested_hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_hosts.append(request.url.host)
        return httpx.Response(302, headers={"Location": "https://evil.example/steal"})

    redirecting_client = httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
    )
    client = GraphClient(StubTokenProvider(), http_client=redirecting_client)

    with pytest.raises(GraphApiError) as exc_info:
        client.fetch_export_data()
    assert exc_info.value.status_code == 302
    assert requested_hosts == ["graph.microsoft.com"]


@pytest.mark.parametrize(
    ("status", "retry_after", "expected_delay"),
    [
        (429, "2", 2.0),
        (429, format_datetime(FIXED_NOW.replace(second=35), usegmt=True), 5.0),
        (503, None, 0.5),
    ],
)
def test_retries_transient_responses(
    status: int,
    retry_after: str | None,
    expected_delay: float,
) -> None:
    attempts = 0
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            headers = {"Retry-After": retry_after} if retry_after is not None else {}
            return httpx.Response(status, headers=headers, json={"error": {"code": "busy"}})
        return httpx.Response(200, json={"value": []})

    client = GraphClient(
        StubTokenProvider(),
        http_client=_http_client(handler),
        sleeper=delays.append,
        clock=lambda: FIXED_NOW.replace(second=30),
    )

    result = client.fetch_export_data()

    assert result.sources == ()
    assert attempts == 2
    assert delays == [expected_delay]


def test_retries_transport_error() -> None:
    attempts = 0
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectError("offline", request=request)
        return httpx.Response(200, json={"value": []})

    client = GraphClient(
        StubTokenProvider(),
        http_client=_http_client(handler),
        sleeper=delays.append,
    )

    assert client.fetch_export_data().sources == ()
    assert attempts == 2
    assert delays == [0.5]


def test_transport_error_after_retry_budget_is_structured() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    client = GraphClient(
        StubTokenProvider(),
        http_client=_http_client(handler),
        max_retries=0,
    )

    with pytest.raises(GraphTransportError) as exc_info:
        client.fetch_export_data()
    assert "offline" in str(exc_info.value)


def test_unauthorized_response_forces_one_token_refresh() -> None:
    provider = StubTokenProvider()
    auth_headers: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        auth_headers.append(request.headers["Authorization"])
        if len(auth_headers) == 1:
            return httpx.Response(401, json={"error": {"code": "InvalidAuthenticationToken"}})
        return httpx.Response(200, json={"value": []})

    client = GraphClient(provider, http_client=_http_client(handler))

    assert client.fetch_export_data().sources == ()
    assert provider.calls == [False, True]
    assert auth_headers == ["Bearer cached-token", "Bearer fresh-token"]


def test_graph_api_error_exposes_structured_details() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            headers={"request-id": "header-request-id"},
            json={
                "error": {
                    "code": "AccessDenied",
                    "message": "Permission denied",
                    "innerError": {"request-id": "body-request-id"},
                }
            },
        )

    client = GraphClient(StubTokenProvider(), http_client=_http_client(handler))

    with pytest.raises(GraphApiError) as exc_info:
        client.fetch_export_data()
    error = exc_info.value
    assert error.status_code == 403
    assert error.code == "AccessDenied"
    assert error.detail == "Permission denied"
    assert error.request_id == "header-request-id"


@pytest.mark.parametrize(
    "response_factory",
    [
        lambda: httpx.Response(200, content=b"not json"),
        lambda: httpx.Response(200, json=[]),
        lambda: httpx.Response(200, json={"other": []}),
        lambda: httpx.Response(200, json={"value": ["not an object"]}),
        lambda: httpx.Response(200, json={"value": [], "@odata.nextLink": 123}),
    ],
)
def test_rejects_malformed_page_envelopes(
    response_factory: Callable[[], httpx.Response],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return response_factory()

    client = GraphClient(StubTokenProvider(), http_client=_http_client(handler))

    with pytest.raises(GraphProtocolError):
        client.fetch_export_data()


def test_rejects_list_without_string_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"value": [{"displayName": "No id"}]})

    client = GraphClient(StubTokenProvider(), http_client=_http_client(handler))

    with pytest.raises(GraphProtocolError):
        client.fetch_export_data()


def test_encodes_task_id_when_retrieving_checklists() -> None:
    requested_paths: list[str] = []
    task_id = "task/a?=æ"
    encoded_task_id = "task%2Fa%3F%3D%C3%A6"

    def handler(request: httpx.Request) -> httpx.Response:
        raw_path = request.url.raw_path.decode("ascii")
        requested_paths.append(raw_path)
        if raw_path == "/v1.0/me/todo/lists":
            return httpx.Response(200, json={"value": [{"id": "L1", "displayName": "List"}]})
        if raw_path == "/v1.0/me/todo/lists/L1/tasks":
            return httpx.Response(200, json={"value": [_task(task_id)]})
        if raw_path == f"/v1.0/me/todo/lists/L1/tasks/{encoded_task_id}/checklistItems":
            return httpx.Response(200, json={"value": []})
        raise AssertionError(f"unexpected request: {request.url}")

    result = GraphClient(StubTokenProvider(), http_client=_http_client(handler)).fetch_export_data()

    assert requested_paths[-1].endswith(f"/{encoded_task_id}/checklistItems")
    assert result.sources[0].raw_checklists[0].task_id == task_id


def test_rejects_task_without_string_id_before_checklist_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raw_path = request.url.raw_path.decode("ascii")
        if raw_path == "/v1.0/me/todo/lists":
            return httpx.Response(200, json={"value": [{"id": "L1", "displayName": "List"}]})
        if raw_path == "/v1.0/me/todo/lists/L1/tasks":
            return httpx.Response(200, json={"value": [{"title": "No id"}]})
        raise AssertionError(f"unexpected request: {request.url}")

    client = GraphClient(StubTokenProvider(), http_client=_http_client(handler))

    with pytest.raises(GraphProtocolError, match="task entity is missing"):
        client.fetch_export_data()
