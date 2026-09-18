"""Convert raw Microsoft Graph dictionaries into normalized domain values."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .models import ChecklistItem, GraphDateTime, GraphInstant, TaskBody, TodoList, TodoTask


class NormalizationError(ValueError):
    """Raised when required Graph data has an unexpected shape."""


def _required_string(data: Mapping[str, Any], name: str, *, context: str) -> str:
    value = data.get(name)
    if not isinstance(value, str):
        raise NormalizationError(f"{context}.{name} must be a string")
    return value


def _date_time_timezone(
    data: Mapping[str, Any], name: str, *, context: str
) -> GraphDateTime | None:
    value = data.get(name)
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise NormalizationError(f"{context}.{name} must be an object or null")
    try:
        return GraphDateTime(
            date_time=_required_string(value, "dateTime", context=f"{context}.{name}"),
            time_zone=_required_string(value, "timeZone", context=f"{context}.{name}"),
        )
    except ValueError as error:
        raise NormalizationError(f"invalid {context}.{name}: {error}") from error


def _checklist_item(raw: Mapping[str, Any], *, context: str) -> ChecklistItem:
    is_checked = raw.get("isChecked")
    if not isinstance(is_checked, bool):
        raise NormalizationError(f"{context}.isChecked must be a boolean")
    return ChecklistItem(
        source_id=_required_string(raw, "id", context=context),
        display_name=_required_string(raw, "displayName", context=context),
        is_checked=is_checked,
    )


def normalize_task(
    raw: Mapping[str, Any],
    *,
    list_id: str,
    raw_checklist_items: Sequence[Mapping[str, Any]] = (),
) -> TodoTask:
    """Normalize one raw Graph todoTask without consulting local timezone state."""
    context = f"task[{raw.get('id', '?')}]"

    body_value = raw.get("body")
    if body_value is None:
        body = TaskBody(content="", content_type="text")
    elif isinstance(body_value, Mapping):
        body = TaskBody(
            content=_required_string(body_value, "content", context=f"{context}.body"),
            content_type=_required_string(body_value, "contentType", context=f"{context}.body"),
        )
    else:
        raise NormalizationError(f"{context}.body must be an object or null")

    category_value = raw.get("categories", [])
    if not isinstance(category_value, Sequence) or isinstance(category_value, (str, bytes)):
        raise NormalizationError(f"{context}.categories must be an array")
    if not all(isinstance(category, str) for category in category_value):
        raise NormalizationError(f"{context}.categories must contain only strings")

    try:
        created_at = GraphInstant(_required_string(raw, "createdDateTime", context=context))
        modified_at = GraphInstant(_required_string(raw, "lastModifiedDateTime", context=context))
    except ValueError as error:
        raise NormalizationError(f"invalid {context} timestamp: {error}") from error

    return TodoTask(
        source_id=_required_string(raw, "id", context=context),
        list_id=list_id,
        title=_required_string(raw, "title", context=context),
        body=body,
        status=_required_string(raw, "status", context=context),
        importance=_required_string(raw, "importance", context=context),
        created_at=created_at,
        modified_at=modified_at,
        completed_at=_date_time_timezone(raw, "completedDateTime", context=context),
        due_at=_date_time_timezone(raw, "dueDateTime", context=context),
        start_at=_date_time_timezone(raw, "startDateTime", context=context),
        categories=tuple(category_value),
        checklist_items=tuple(
            _checklist_item(item, context=f"{context}.checklistItems[{index}]")
            for index, item in enumerate(raw_checklist_items)
        ),
    )


def normalize_list(
    raw_list: Mapping[str, Any],
    raw_tasks: Sequence[Mapping[str, Any]],
    raw_checklists: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
) -> TodoList:
    """Normalize a task list and its already-retrieved raw tasks."""
    list_id = _required_string(raw_list, "id", context="list")
    return TodoList(
        source_id=list_id,
        display_name=_required_string(raw_list, "displayName", context=f"list[{list_id}]"),
        tasks=tuple(
            normalize_task(
                task,
                list_id=list_id,
                raw_checklist_items=(raw_checklists or {}).get(
                    _required_string(task, "id", context="task"), ()
                ),
            )
            for task in raw_tasks
        ),
    )
