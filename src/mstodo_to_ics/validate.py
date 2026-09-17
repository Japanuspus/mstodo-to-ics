"""Parse-back and semantic validation for generated calendars."""

from __future__ import annotations

from dataclasses import dataclass

from icalendar import Calendar

from .ics import task_uid
from .models import TodoList


class CalendarValidationError(ValueError):
    """Raised when generated calendar data fails migration invariants."""


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """Summary of a successful calendar validation."""

    parent_vtodo_count: int
    child_vtodo_count: int
    uids: tuple[str, ...]


def _required_text(component: object, name: str) -> str:
    getter = getattr(component, "get", None)
    if getter is None:
        raise CalendarValidationError(f"component cannot provide required property {name}")
    value = getter(name)
    if value is None or not str(value):
        raise CalendarValidationError(f"VTODO is missing required property {name}")
    return str(value)


def validate_calendar(data: bytes, expected: TodoList) -> ValidationReport:
    """Parse a calendar and verify the parent-task invariants for this milestone."""
    try:
        calendar = Calendar.from_ical(data)
    except Exception as error:
        raise CalendarValidationError(f"calendar is not parseable: {error}") from error

    todos = tuple(calendar.walk("VTODO"))
    if len(todos) != len(expected.tasks):
        raise CalendarValidationError(
            f"expected {len(expected.tasks)} parent VTODOs, found {len(todos)}"
        )

    uids = tuple(_required_text(todo, "UID") for todo in todos)
    if len(set(uids)) != len(uids):
        raise CalendarValidationError("calendar contains duplicate VTODO UIDs")

    expected_uids = {task_uid(task.list_id, task.source_id) for task in expected.tasks}
    if set(uids) != expected_uids:
        raise CalendarValidationError("calendar VTODO UIDs do not match the source tasks")

    for todo in todos:
        list_id = _required_text(todo, "X-MSTODO-LIST-ID")
        task_id = _required_text(todo, "X-MSTODO-TASK-ID")
        uid = _required_text(todo, "UID")
        if list_id != expected.source_id:
            raise CalendarValidationError(f"VTODO {uid} has the wrong source list ID")
        if uid != task_uid(list_id, task_id):
            raise CalendarValidationError(f"VTODO {uid} does not match its source IDs")

    return ValidationReport(
        parent_vtodo_count=len(todos),
        child_vtodo_count=0,
        uids=uids,
    )
