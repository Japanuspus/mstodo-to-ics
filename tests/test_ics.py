from __future__ import annotations

from dataclasses import replace
from typing import Any, cast

from icalendar import Calendar

from mstodo_to_ics.ics import serialize_calendar, task_uid
from mstodo_to_ics.models import ChecklistItem, TodoList
from mstodo_to_ics.validate import validate_calendar


def test_serializes_and_validates_parent_vtodos(normalized_list: TodoList) -> None:
    data = serialize_calendar(normalized_list)
    report = validate_calendar(data, normalized_list)

    assert report.parent_vtodo_count == 2
    assert report.child_vtodo_count == 0
    assert set(report.uids) == {
        task_uid(normalized_list.source_id, task.source_id) for task in normalized_list.tasks
    }


def test_calendar_preserves_wall_times_and_explicit_timezone(normalized_list: TodoList) -> None:
    text = serialize_calendar(normalized_list).decode("utf-8")

    assert "DTSTART;TZID=Europe/Copenhagen:20261024T091500" in text
    assert "DUE;TZID=Europe/Copenhagen:20261025T023000" in text
    assert "X-MSTODO-DUE-DATETIME:2026-10-25T02:30:00.0000000" in text
    assert "X-MSTODO-DUE-TIMEZONE:Europe/Copenhagen" in text
    assert "DUE:20261025T023000Z" not in text


def test_calendar_maps_status_priority_categories_and_unicode(normalized_list: TodoList) -> None:
    calendar = Calendar.from_ical(serialize_calendar(normalized_list))
    todos = tuple(calendar.walk("VTODO"))
    active, completed = todos

    assert str(active["SUMMARY"]) == "Køb æbler, mælk; og brød \\ ø"
    assert str(active["DESCRIPTION"]) == (
        "Første linje\nAnden linje med æ ø å Æ Ø Å, semikolon; og \\."
    )
    assert str(active["STATUS"]) == "NEEDS-ACTION"
    assert cast(int, active.decoded("PRIORITY")) == 1
    assert cast(list[str], active.decoded("CATEGORIES")) == ["Hjem", "Haster, snart"]
    assert str(completed["STATUS"]) == "COMPLETED"
    assert cast(int, completed.decoded("PRIORITY")) == 5
    assert completed.get("COMPLETED") is not None
    assert "COMPLETED:20260916T184500Z" in serialize_calendar(normalized_list).decode("utf-8")
    assert str(completed["X-MSTODO-COMPLETED-TIMEZONE"]) == "Europe/Copenhagen"


def test_uid_and_serialization_are_deterministic(normalized_list: TodoList) -> None:
    first = serialize_calendar(normalized_list)
    second = serialize_calendar(normalized_list)

    assert first == second
    assert task_uid("list", "task") == task_uid("list", "task")
    assert task_uid("list-a", "task") != task_uid("list-b", "task")


def test_checklist_is_rendered_in_parent_description_without_child_vtodo(
    normalized_list: TodoList,
) -> None:
    parent = replace(
        normalized_list.tasks[0],
        checklist_items=(
            ChecklistItem("check-1", "Køb maling", False),
            ChecklistItem("check-2", "Mål væg\nog loft", True),
        ),
    )
    task_list = replace(normalized_list, tasks=(parent,))

    calendar = Calendar.from_ical(serialize_calendar(task_list))
    todos = calendar.walk("VTODO")

    assert len(todos) == 1
    todo = cast(Any, todos[0])
    assert str(todo["DESCRIPTION"]) == (
        "Første linje\nAnden linje med æ ø å Æ Ø Å, semikolon; og \\.\n\n"
        "Checklist:\n- [ ] Køb maling\n- [x] Mål væg\n  og loft"
    )
    assert todo.get("RELATED-TO") is None
