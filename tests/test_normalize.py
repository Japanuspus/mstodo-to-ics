from __future__ import annotations

from datetime import UTC

import pytest

from mstodo_to_ics.models import GraphDateTime, GraphInstant, TodoList


def test_normalization_preserves_graph_datetime_timezone(normalized_list: TodoList) -> None:
    active = normalized_list.tasks[0]

    assert active.due_at is not None
    assert active.due_at.date_time == "2026-10-25T02:30:00.0000000"
    assert active.due_at.time_zone == "Europe/Copenhagen"
    assert active.due_at.wall_time.tzinfo is None
    assert active.start_at is not None
    assert active.start_at.date_time == "2026-10-24T09:15:00.0000000"


def test_normalization_preserves_instant_source_precision(normalized_list: TodoList) -> None:
    created = normalized_list.tasks[0].created_at

    assert created.source == "2026-09-17T08:00:00.1234567Z"
    assert created.utc.tzinfo is UTC
    assert created.utc.hour == 8


def test_normalizes_completed_task_without_inventing_optional_dates(
    normalized_list: TodoList,
) -> None:
    completed = normalized_list.tasks[1]

    assert completed.status == "completed"
    assert completed.completed_at is not None
    assert completed.due_at is None
    assert completed.start_at is None


def test_graph_instant_rejects_machine_local_datetime() -> None:
    with pytest.raises(ValueError, match="must include an offset"):
        GraphInstant("2026-09-17T08:00:00")


def test_graph_datetime_rejects_embedded_offset() -> None:
    with pytest.raises(ValueError, match="without an offset"):
        GraphDateTime("2026-09-17T08:00:00+02:00", "Europe/Copenhagen")


def test_graph_datetime_resolves_only_its_declared_timezone() -> None:
    value = GraphDateTime("2026-09-16T20:45:00.0000000", "Europe/Copenhagen")

    assert value.as_aware().astimezone(UTC).isoformat() == "2026-09-16T18:45:00+00:00"


def test_unknown_timezone_fails_instead_of_using_machine_local_timezone() -> None:
    value = GraphDateTime("2026-09-16T20:45:00", "Not/A-Timezone")

    with pytest.raises(ValueError, match="unsupported Graph timezone"):
        value.as_aware()
