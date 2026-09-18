from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from icalendar import Calendar

from mstodo_to_ics.export import (
    ExportError,
    RawChecklistExport,
    RawListExport,
    export_bundle,
    plan_export,
)
from mstodo_to_ics.validate import CalendarValidationError

FIXED_TIME = datetime(2026, 9, 18, 12, 30, tzinfo=UTC)


def _task(
    task_id: str,
    *,
    title: str,
    status: str = "notStarted",
    body: str = "",
    completed: bool = False,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    task: dict[str, Any] = {
        "id": task_id,
        "title": title,
        "body": {"content": body, "contentType": "text"},
        "status": status,
        "importance": "normal",
        "createdDateTime": "2026-09-01T08:00:00.1234567Z",
        "lastModifiedDateTime": "2026-09-17T15:00:00Z",
        "completedDateTime": (
            {"dateTime": "2026-09-17T17:00:00.0000000", "timeZone": "Europe/Copenhagen"}
            if completed
            else None
        ),
        "dueDateTime": None,
        "startDateTime": None,
        "categories": [],
    }
    if extra:
        task.update(extra)
    return task


@pytest.fixture
def export_sources() -> tuple[RawListExport, ...]:
    active = _task(
        "active/1",
        title="Planlæg æbler",
        body="Første linje\nAnden linje",
        extra={
            "recurrence": {
                "pattern": {"type": "weekly", "interval": 1},
                "range": {"type": "noEnd", "startDate": "2026-09-18"},
            },
            "isReminderOn": True,
            "reminderDateTime": {
                "dateTime": "2026-09-18T18:00:00.0000000",
                "timeZone": "Europe/Copenhagen",
            },
            "hasAttachments": True,
            "futureGraphField": {"unicode": "æ ø å", "kept": [1, True, None]},
        },
    )
    completed = _task(
        "completed-2",
        title="Færdig",
        status="completed",
        completed=True,
    )
    return (
        RawListExport.from_raw(
            {
                "id": "list/house?=1",
                "displayName": "House",
                "isOwner": True,
                "unknownListField": "behold mig",
            },
            [active, completed],
            raw_checklists=[
                RawChecklistExport.from_raw(
                    "active/1",
                    [
                        {
                            "id": "check-1",
                            "displayName": "Køb maling",
                            "isChecked": False,
                        },
                        {
                            "id": "check-2",
                            "displayName": "Mål væg",
                            "isChecked": True,
                        },
                    ],
                ),
                RawChecklistExport.from_raw("completed-2", []),
            ],
            checklists_collected=True,
        ),
        RawListExport.from_raw(
            {"id": "list-house-2", "displayName": "house", "isOwner": True},
            [],
            checklists_collected=True,
        ),
        RawListExport.from_raw(
            {"id": "empty-list", "displayName": "Tom liste", "isOwner": True},
            [],
            checklists_collected=True,
        ),
    )


def _fixed_clock() -> datetime:
    return FIXED_TIME


def test_publishes_complete_bundle_with_raw_data_and_manifest(
    tmp_path: Path, export_sources: tuple[RawListExport, ...]
) -> None:
    destination = tmp_path / "todo-export"

    plan = export_bundle(destination, export_sources, clock=_fixed_clock)

    assert destination.is_dir()
    published_paths = {
        path.relative_to(destination).as_posix()
        for path in destination.rglob("*")
        if path.is_file()
    }
    assert published_paths == {path.as_posix() for path in plan.relative_paths}

    manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["exported_at"] == "2026-09-18T12:30:00Z"
    assert manifest["summary"] == {
        "lists": 3,
        "tasks": 2,
        "completed_tasks": 1,
        "recurring_tasks_detected": 1,
        "tasks_with_reminders_detected": 1,
        "tasks_with_attachments_detected": 1,
        "unknown_statuses_detected": 0,
        "checklist_items": 2,
        "linked_resources": None,
        "attachment_metadata": None,
    }
    assert manifest["collection_status"] == {
        "checklist_items": "collected",
        "linked_resources": "not_collected",
        "attachment_metadata": "not_collected",
    }
    assert len(manifest["warnings"]) == 3
    assert manifest["errors"] == []

    list_entries = manifest["lists"]
    calendar_names = [entry["calendar_file"] for entry in list_entries]
    assert len({name.casefold() for name in calendar_names}) == 3
    assert all(entry["validation"]["status"] == "passed" for entry in list_entries)

    for entry in list_entries:
        calendar = Calendar.from_ical((destination / entry["calendar_file"]).read_bytes())
        assert len(calendar.walk("VTODO")) == entry["tasks"]

    raw_lists = json.loads((destination / "raw" / "lists.json").read_text(encoding="utf-8"))
    assert raw_lists == [source.raw_list for source in export_sources]
    first_raw_tasks_path = destination / list_entries[0]["raw_tasks_file"]
    first_raw_tasks = json.loads(first_raw_tasks_path.read_text(encoding="utf-8"))
    assert first_raw_tasks == list(export_sources[0].raw_tasks)
    assert first_raw_tasks[0]["futureGraphField"] == {
        "kept": [1, True, None],
        "unicode": "æ ø å",
    }
    first_raw_checklists_path = destination / list_entries[0]["raw_checklists_file"]
    first_raw_checklists = json.loads(first_raw_checklists_path.read_text(encoding="utf-8"))
    assert first_raw_checklists[0]["items"][1] == {
        "displayName": "Mål væg",
        "id": "check-2",
        "isChecked": True,
    }

    first_calendar = Calendar.from_ical(
        (destination / list_entries[0]["calendar_file"]).read_bytes()
    )
    active_todo = cast(Any, first_calendar.walk("VTODO")[0])
    assert str(active_todo["DESCRIPTION"]) == (
        "Første linje\nAnden linje\n\nChecklist:\n- [ ] Køb maling\n- [x] Mål væg"
    )


def test_plan_is_deterministic_with_injected_timestamp(
    export_sources: tuple[RawListExport, ...],
) -> None:
    first = plan_export(export_sources, exported_at=FIXED_TIME)
    second = plan_export(export_sources, exported_at=FIXED_TIME)

    assert first.manifest == second.manifest
    assert first.relative_paths == second.relative_paths
    assert [item.content for item in first.files] == [item.content for item in second.files]


def test_dry_run_writes_nothing(tmp_path: Path, export_sources: tuple[RawListExport, ...]) -> None:
    destination = tmp_path / "not-created" / "todo-export"

    plan = export_bundle(destination, export_sources, dry_run=True, clock=_fixed_clock)

    assert not destination.exists()
    assert not destination.parent.exists()
    assert Path("manifest.json") in plan.relative_paths


def test_existing_destination_is_not_modified(
    tmp_path: Path, export_sources: tuple[RawListExport, ...]
) -> None:
    destination = tmp_path / "existing"
    destination.mkdir()
    sentinel = destination / "keep.txt"
    sentinel.write_text("untouched", encoding="utf-8")

    with pytest.raises(ExportError, match="already exists"):
        export_bundle(destination, export_sources, clock=_fixed_clock)

    assert sentinel.read_text(encoding="utf-8") == "untouched"
    assert tuple(destination.iterdir()) == (sentinel,)


def test_validation_failure_leaves_no_partial_bundle(tmp_path: Path) -> None:
    duplicate = _task("same-id", title="Duplicate")
    source = RawListExport.from_raw(
        {"id": "duplicate-task-list", "displayName": "Duplicates"},
        [duplicate, dict(duplicate)],
    )
    destination = tmp_path / "must-not-exist"

    with pytest.raises(CalendarValidationError, match="duplicate VTODO UIDs"):
        export_bundle(destination, [source], clock=_fixed_clock)

    assert not destination.exists()
    assert not tuple(tmp_path.glob(".must-not-exist.staging-*"))


def test_long_description_is_folded_and_round_trips() -> None:
    description = ("Lang beskrivelse med æ ø å, semikolon; og \\.\n" * 20).rstrip()
    source = RawListExport.from_raw(
        {"id": "long-list", "displayName": "Long"},
        [_task("long-task", title="Lang", body=description)],
    )

    plan = plan_export([source], exported_at=FIXED_TIME)
    calendar_file = next(item for item in plan.files if item.relative_path.suffix == ".ics")

    assert b"\r\n " in calendar_file.content
    calendar = Calendar.from_ical(calendar_file.content)
    todo = cast(Any, calendar.walk("VTODO")[0])
    assert str(todo["DESCRIPTION"]) == description


def test_invalid_raw_json_fails_before_publication(tmp_path: Path) -> None:
    source = RawListExport.from_raw(
        {"id": "invalid-json", "displayName": "Invalid", "notJson": {"a", "set"}},
        [],
    )
    destination = tmp_path / "invalid"

    with pytest.raises(ExportError, match="not valid JSON"):
        export_bundle(destination, [source], clock=_fixed_clock)

    assert not destination.exists()
