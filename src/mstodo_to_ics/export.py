"""Plan and transactionally publish offline migration export bundles."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import __version__
from .filenames import allocate_list_filenames, stable_source_key
from .ics import serialize_calendar
from .models import TodoList
from .normalize import normalize_list
from .validate import ValidationReport, validate_calendar

Clock = Callable[[], datetime]

_KNOWN_STATUSES = {"notStarted", "inProgress", "completed", "waitingOnOthers", "deferred"}


class ExportError(RuntimeError):
    """Raised when an export bundle cannot be safely planned or published."""


@dataclass(frozen=True, slots=True)
class RawChecklistExport:
    """Decoded checklist entities and page envelopes for one parent task."""

    task_id: str
    raw_items: tuple[Mapping[str, Any], ...]
    raw_pages: tuple[Mapping[str, Any], ...] = ()

    @classmethod
    def from_raw(
        cls,
        task_id: str,
        raw_items: Sequence[Mapping[str, Any]],
        *,
        raw_pages: Sequence[Mapping[str, Any]] = (),
    ) -> RawChecklistExport:
        return cls(task_id=task_id, raw_items=tuple(raw_items), raw_pages=tuple(raw_pages))


@dataclass(frozen=True, slots=True)
class RawListExport:
    """Decoded Graph entities for one list, retaining every JSON field."""

    raw_list: Mapping[str, Any]
    raw_tasks: tuple[Mapping[str, Any], ...]
    raw_task_pages: tuple[Mapping[str, Any], ...] = ()
    raw_checklists: tuple[RawChecklistExport, ...] = ()
    checklists_collected: bool = False

    @classmethod
    def from_raw(
        cls,
        raw_list: Mapping[str, Any],
        raw_tasks: Sequence[Mapping[str, Any]],
        *,
        raw_task_pages: Sequence[Mapping[str, Any]] = (),
        raw_checklists: Sequence[RawChecklistExport] = (),
        checklists_collected: bool = False,
    ) -> RawListExport:
        """Create a source while preserving entity dictionaries unchanged."""
        return cls(
            raw_list=raw_list,
            raw_tasks=tuple(raw_tasks),
            raw_task_pages=tuple(raw_task_pages),
            raw_checklists=tuple(raw_checklists),
            checklists_collected=checklists_collected,
        )


@dataclass(frozen=True, slots=True)
class RawExportData:
    """All decoded entities plus original list collection page envelopes."""

    sources: tuple[RawListExport, ...]
    raw_list_pages: tuple[Mapping[str, Any], ...] = ()

    @classmethod
    def from_sources(cls, sources: Sequence[RawListExport]) -> RawExportData:
        """Wrap manually supplied sources that have no Graph page envelopes."""
        return cls(sources=tuple(sources))


type ExportInput = Sequence[RawListExport] | RawExportData


@dataclass(frozen=True, slots=True)
class PlannedFile:
    """One relative output path and its complete content."""

    relative_path: Path
    content: bytes


@dataclass(frozen=True, slots=True)
class ExportPlan:
    """A fully validated bundle that can be inspected or published."""

    exported_at: datetime
    files: tuple[PlannedFile, ...]
    manifest: Mapping[str, Any]

    @property
    def relative_paths(self) -> tuple[Path, ...]:
        """Return every path the publisher will create."""
        return tuple(planned.relative_path for planned in self.files)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _format_timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ExportError("export timestamp must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _json_bytes(value: object) -> bytes:
    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise ExportError(f"raw data is not valid JSON: {error}") from error
    return (text + "\n").encode("utf-8")


def _source_id(source: RawListExport) -> str:
    source_id = source.raw_list.get("id")
    if not isinstance(source_id, str):
        raise ExportError("every raw list must contain a string id")
    return source_id


def _checklist_mapping(source: RawListExport) -> dict[str, tuple[Mapping[str, Any], ...]]:
    task_ids = {task.get("id") for task in source.raw_tasks if isinstance(task.get("id"), str)}
    result: dict[str, tuple[Mapping[str, Any], ...]] = {}
    for checklist in source.raw_checklists:
        if checklist.task_id not in task_ids:
            raise ExportError(f"checklist data references unknown task ID: {checklist.task_id}")
        if checklist.task_id in result:
            raise ExportError(f"duplicate checklist data for task ID: {checklist.task_id}")
        result[checklist.task_id] = checklist.raw_items
    if source.checklists_collected and set(result) != task_ids:
        raise ExportError("collected checklist data must contain one entry for every task")
    if not source.checklists_collected and result:
        raise ExportError("checklist data cannot be present when collection is marked incomplete")
    return result


def _feature_counts(raw_tasks: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    return {
        "completed_tasks": sum(task.get("status") == "completed" for task in raw_tasks),
        "recurring_tasks_detected": sum(task.get("recurrence") is not None for task in raw_tasks),
        "tasks_with_reminders_detected": sum(
            task.get("isReminderOn") is True for task in raw_tasks
        ),
        "tasks_with_attachments_detected": sum(
            task.get("hasAttachments") is True for task in raw_tasks
        ),
        "unknown_statuses_detected": sum(
            isinstance(task.get("status"), str) and task.get("status") not in _KNOWN_STATUSES
            for task in raw_tasks
        ),
    }


def _warning_messages(summary: Mapping[str, int]) -> list[str]:
    warnings: list[str] = []
    warning_specs = (
        (
            "recurring_tasks_detected",
            "recurring task(s) detected; recurrence is preserved in raw JSON but v1 does not "
            "map it to RRULE",
        ),
        (
            "tasks_with_reminders_detected",
            "task(s) with reminders detected; reminders are preserved in raw JSON but v1 does "
            "not map them to VALARM",
        ),
        (
            "tasks_with_attachments_detected",
            "task(s) with attachments detected, but attachment metadata was not collected",
        ),
        (
            "unknown_statuses_detected",
            "task(s) with unknown statuses detected; ICS uses NEEDS-ACTION as a fallback",
        ),
    )
    for key, message in warning_specs:
        count = summary[key]
        if count:
            warnings.append(f"{count} {message}.")
    return warnings


def _validation_manifest(report: ValidationReport) -> dict[str, int | str]:
    return {
        "status": "passed",
        "parent_vtodos": report.parent_vtodo_count,
        "child_vtodos": report.child_vtodo_count,
    }


def _export_data(value: ExportInput) -> RawExportData:
    if isinstance(value, RawExportData):
        return value
    return RawExportData.from_sources(value)


def plan_export(sources: ExportInput, *, exported_at: datetime) -> ExportPlan:
    """Build and validate every output file without touching the filesystem."""
    export_data = _export_data(sources)
    source_items = export_data.sources
    exported_at_text = _format_timestamp(exported_at)
    source_ids = tuple(_source_id(source) for source in source_items)
    if len(source_ids) != len(set(source_ids)):
        raise ExportError("raw export contains duplicate list IDs")

    checklist_mappings = tuple(_checklist_mapping(source) for source in source_items)
    normalized: tuple[TodoList, ...] = tuple(
        normalize_list(source.raw_list, source.raw_tasks, checklists)
        for source, checklists in zip(source_items, checklist_mappings, strict=True)
    )
    calendar_names = allocate_list_filenames(normalized)

    planned_files: list[PlannedFile] = []
    manifest_lists: list[dict[str, Any]] = []
    aggregate = {
        "completed_tasks": 0,
        "recurring_tasks_detected": 0,
        "tasks_with_reminders_detected": 0,
        "tasks_with_attachments_detected": 0,
        "unknown_statuses_detected": 0,
    }

    total_checklist_items = 0
    for source, task_list in zip(source_items, normalized, strict=True):
        calendar_data = serialize_calendar(task_list)
        validation = validate_calendar(calendar_data, task_list)
        calendar_path = Path(calendar_names[task_list.source_id])
        raw_tasks_path = Path("raw", "tasks", f"{stable_source_key(task_list.source_id)}.json")
        raw_task_page_paths = tuple(
            Path(
                "raw",
                "pages",
                "tasks",
                stable_source_key(task_list.source_id),
                f"{page_number:04d}.json",
            )
            for page_number in range(1, len(source.raw_task_pages) + 1)
        )
        raw_checklists_path = Path(
            "raw", "checklists", f"{stable_source_key(task_list.source_id)}.json"
        )
        raw_checklist_page_entries: list[dict[str, Any]] = []
        checklist_count = sum(len(checklist.raw_items) for checklist in source.raw_checklists)
        total_checklist_items += checklist_count
        counts = _feature_counts(source.raw_tasks)
        for key in aggregate:
            aggregate[key] += counts[key]

        planned_files.extend(
            (
                PlannedFile(calendar_path, calendar_data),
                PlannedFile(raw_tasks_path, _json_bytes(list(source.raw_tasks))),
            )
        )
        planned_files.extend(
            PlannedFile(path, _json_bytes(page))
            for path, page in zip(raw_task_page_paths, source.raw_task_pages, strict=True)
        )
        if source.checklists_collected:
            planned_files.append(
                PlannedFile(
                    raw_checklists_path,
                    _json_bytes(
                        [
                            {"task_id": checklist.task_id, "items": list(checklist.raw_items)}
                            for checklist in source.raw_checklists
                        ]
                    ),
                )
            )
            for checklist in source.raw_checklists:
                page_paths = tuple(
                    Path(
                        "raw",
                        "pages",
                        "checklists",
                        stable_source_key(task_list.source_id),
                        stable_source_key(checklist.task_id),
                        f"{page_number:04d}.json",
                    )
                    for page_number in range(1, len(checklist.raw_pages) + 1)
                )
                planned_files.extend(
                    PlannedFile(path, _json_bytes(page))
                    for path, page in zip(page_paths, checklist.raw_pages, strict=True)
                )
                raw_checklist_page_entries.append(
                    {
                        "task_id": checklist.task_id,
                        "files": [path.as_posix() for path in page_paths],
                    }
                )
        manifest_lists.append(
            {
                "source_id": task_list.source_id,
                "display_name": task_list.display_name,
                "calendar_file": calendar_path.as_posix(),
                "raw_tasks_file": raw_tasks_path.as_posix(),
                "raw_task_page_files": [path.as_posix() for path in raw_task_page_paths],
                "raw_checklists_file": (
                    raw_checklists_path.as_posix() if source.checklists_collected else None
                ),
                "raw_checklist_page_files": raw_checklist_page_entries,
                "tasks": len(task_list.tasks),
                "checklist_items": checklist_count if source.checklists_collected else None,
                **counts,
                "validation": _validation_manifest(validation),
            }
        )

    raw_lists_path = Path("raw", "lists.json")
    planned_files.append(
        PlannedFile(raw_lists_path, _json_bytes([source.raw_list for source in source_items]))
    )
    raw_list_page_paths = tuple(
        Path("raw", "pages", "lists", f"{page_number:04d}.json")
        for page_number in range(1, len(export_data.raw_list_pages) + 1)
    )
    planned_files.extend(
        PlannedFile(path, _json_bytes(page))
        for path, page in zip(raw_list_page_paths, export_data.raw_list_pages, strict=True)
    )

    summary: dict[str, int | None] = {
        "lists": len(source_items),
        "tasks": sum(len(source.raw_tasks) for source in source_items),
        **aggregate,
        "checklist_items": (
            total_checklist_items
            if all(source.checklists_collected for source in source_items)
            else None
        ),
        "linked_resources": None,
        "attachment_metadata": None,
    }
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "tool": {"name": "mstodo-to-ics", "version": __version__},
        "exported_at": exported_at_text,
        "summary": summary,
        "collection_status": {
            "checklist_items": (
                "collected"
                if all(source.checklists_collected for source in source_items)
                else "not_collected"
            ),
            "linked_resources": "not_collected",
            "attachment_metadata": "not_collected",
        },
        "raw_list_page_files": [path.as_posix() for path in raw_list_page_paths],
        "lists": manifest_lists,
        "warnings": _warning_messages(aggregate),
        "errors": [],
    }
    planned_files.append(PlannedFile(Path("manifest.json"), _json_bytes(manifest)))

    paths = tuple(planned.relative_path.as_posix().casefold() for planned in planned_files)
    if len(paths) != len(set(paths)):
        raise ExportError("export plan contains colliding output paths")

    return ExportPlan(
        exported_at=exported_at.astimezone(UTC),
        files=tuple(sorted(planned_files, key=lambda item: item.relative_path.as_posix())),
        manifest=manifest,
    )


def publish_export(plan: ExportPlan, destination: Path) -> None:
    """Publish a validated plan atomically, refusing to overwrite a destination."""
    destination = destination.absolute()
    if destination.exists() or destination.is_symlink():
        raise ExportError(f"export destination already exists: {destination}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.staging-", dir=destination.parent))
    try:
        for planned in plan.files:
            output_path = staging / planned.relative_path
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(planned.content)
        os.replace(staging, destination)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def export_bundle(
    destination: Path,
    sources: ExportInput,
    *,
    dry_run: bool = False,
    clock: Clock = _utc_now,
) -> ExportPlan:
    """Plan an offline bundle and publish it unless ``dry_run`` is true."""
    plan = plan_export(sources, exported_at=clock())
    if not dry_run:
        publish_export(plan, destination)
    return plan
