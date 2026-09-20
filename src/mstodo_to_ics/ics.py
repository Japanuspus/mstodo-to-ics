"""Build RFC 5545 calendars from normalized Microsoft To Do values."""

from __future__ import annotations

from datetime import UTC
from uuid import NAMESPACE_URL, UUID, uuid5

from icalendar import Calendar, Todo

from .models import ChecklistItem, GraphDateTime, TodoList, TodoTask

PRODUCT_ID = "-//mstodo-to-ics//EN"
UID_NAMESPACE: UUID = uuid5(NAMESPACE_URL, "https://mstodo-to-ics.invalid/uid/v1")

_STATUS_MAP = {
    "completed": "COMPLETED",
    "inProgress": "IN-PROCESS",
    "notStarted": "NEEDS-ACTION",
    "waitingOnOthers": "NEEDS-ACTION",
    "deferred": "NEEDS-ACTION",
}
_PRIORITY_MAP = {"high": 1, "normal": 0, "low": 9}


def _normalize_newlines(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def task_uid(list_id: str, task_id: str) -> str:
    """Return a stable RFC-friendly UID for a source task."""
    identity = f"list:{list_id}\x1ftask:{task_id}"
    return f"{uuid5(UID_NAMESPACE, identity)}@mstodo-to-ics"


def _add_graph_datetime(todo: Todo, name: str, value: GraphDateTime) -> None:
    if value.time_zone.casefold() in {"utc", "etc/utc", "gmt", "z"}:
        todo.add(name, value.wall_time.replace(tzinfo=UTC))
    else:
        todo.add(name, value.wall_time, parameters={"TZID": value.time_zone})

    source_prefix = f"X-MSTODO-{name}"
    todo.add(f"{source_prefix}-DATETIME", value.date_time)
    todo.add(f"{source_prefix}-TIMEZONE", value.time_zone)


def _checklist_line(item: ChecklistItem) -> str:
    marker = "x" if item.is_checked else " "
    lines = _normalize_newlines(item.display_name).split("\n")
    first, *continuation = lines
    rendered = [f"- [{marker}] {first}"]
    rendered.extend(f"  {line}" for line in continuation)
    return "\n".join(rendered)


def task_description(task: TodoTask) -> str:
    """Return notes followed by the task's human-readable checklist block."""
    sections: list[str] = []
    if task.body.content:
        sections.append(_normalize_newlines(task.body.content))
    if task.checklist_items:
        checklist = "Checklist:\n" + "\n".join(
            _checklist_line(item) for item in task.checklist_items
        )
        sections.append(checklist)
    return "\n\n".join(sections)


def task_to_vtodo(task: TodoTask) -> Todo:
    """Convert a normalized task into a parent VTODO component."""
    todo = Todo()
    todo.add("uid", task_uid(task.list_id, task.source_id))
    todo.add("dtstamp", task.modified_at.utc)
    todo.add("summary", task.title)
    todo.add("created", task.created_at.utc)
    todo.add("last-modified", task.modified_at.utc)
    todo.add("status", _STATUS_MAP.get(task.status, "NEEDS-ACTION"))
    todo.add("X-MSTODO-STATUS", task.status)
    todo.add("X-MSTODO-LIST-ID", task.list_id)
    todo.add("X-MSTODO-TASK-ID", task.source_id)

    description = task_description(task)
    if description:
        todo.add("description", description)
    if task.body.content:
        todo.add("X-MSTODO-BODY-CONTENT-TYPE", task.body.content_type)
    if task.start_at is not None:
        _add_graph_datetime(todo, "DTSTART", task.start_at)
    if task.due_at is not None:
        _add_graph_datetime(todo, "DUE", task.due_at)
    if task.completed_at is not None:
        todo.add("completed", task.completed_at.as_aware().astimezone(UTC))
        todo.add("X-MSTODO-COMPLETED-DATETIME", task.completed_at.date_time)
        todo.add("X-MSTODO-COMPLETED-TIMEZONE", task.completed_at.time_zone)
    if task.importance in _PRIORITY_MAP:
        todo.add("priority", _PRIORITY_MAP[task.importance])
    if task.categories:
        todo.add("categories", list(task.categories))

    return todo


def build_calendar(task_list: TodoList) -> Calendar:
    """Build one calendar containing all parent tasks from one source list."""
    calendar = Calendar()
    calendar.add("prodid", PRODUCT_ID)
    calendar.add("version", "2.0")
    calendar.add("calscale", "GREGORIAN")
    calendar.add("X-WR-CALNAME", task_list.display_name)
    calendar.add("X-MSTODO-LIST-ID", task_list.source_id)
    for task in task_list.tasks:
        calendar.add_component(task_to_vtodo(task))
    return calendar


def serialize_calendar(task_list: TodoList) -> bytes:
    """Serialize one source list into RFC 5545 bytes."""
    return bytes(build_calendar(task_list).to_ical())
