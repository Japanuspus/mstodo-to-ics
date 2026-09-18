"""Transactional storage and loading of durable raw Graph retrieval snapshots."""

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
from .export import RawChecklistExport, RawExportData, RawListExport
from .filenames import stable_source_key

Clock = Callable[[], datetime]
SNAPSHOT_MANIFEST_FILENAME = "retrieval.json"
SNAPSHOT_SCHEMA_VERSION = 1


class SnapshotError(RuntimeError):
    """Raised when a retrieval snapshot cannot be safely stored or loaded."""


@dataclass(frozen=True, slots=True)
class SnapshotFile:
    relative_path: Path
    content: bytes


@dataclass(frozen=True, slots=True)
class SnapshotPlan:
    retrieved_at: datetime
    files: tuple[SnapshotFile, ...]
    manifest: Mapping[str, Any]

    @property
    def relative_paths(self) -> tuple[Path, ...]:
        return tuple(item.relative_path for item in self.files)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _format_timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise SnapshotError("retrieval timestamp must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _json_bytes(value: object) -> bytes:
    try:
        text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise SnapshotError(f"raw data is not valid JSON: {error}") from error
    return (text + "\n").encode()


def _required_id(value: Mapping[str, Any], *, context: str) -> str:
    source_id = value.get("id")
    if not isinstance(source_id, str):
        raise SnapshotError(f"{context} must contain a string id")
    return source_id


def plan_snapshot(data: RawExportData, *, retrieved_at: datetime) -> SnapshotPlan:
    """Build a complete raw snapshot in memory before publication."""
    retrieved_at_text = _format_timestamp(retrieved_at)
    files: list[SnapshotFile] = []
    raw_lists_path = Path("raw", "lists.json")
    files.append(
        SnapshotFile(raw_lists_path, _json_bytes([item.raw_list for item in data.sources]))
    )

    raw_list_page_paths = tuple(
        Path("raw", "pages", "lists", f"{number:04d}.json")
        for number in range(1, len(data.raw_list_pages) + 1)
    )
    files.extend(
        SnapshotFile(path, _json_bytes(page))
        for path, page in zip(raw_list_page_paths, data.raw_list_pages, strict=True)
    )

    list_entries: list[dict[str, Any]] = []
    for source in data.sources:
        list_id = _required_id(source.raw_list, context="list")
        list_key = stable_source_key(list_id)
        raw_tasks_path = Path("raw", "tasks", f"{list_key}.json")
        task_page_paths = tuple(
            Path("raw", "pages", "tasks", list_key, f"{number:04d}.json")
            for number in range(1, len(source.raw_task_pages) + 1)
        )
        files.append(SnapshotFile(raw_tasks_path, _json_bytes(list(source.raw_tasks))))
        files.extend(
            SnapshotFile(path, _json_bytes(page))
            for path, page in zip(task_page_paths, source.raw_task_pages, strict=True)
        )

        raw_checklists_path = Path("raw", "checklists", f"{list_key}.json")
        checklist_page_entries: list[dict[str, Any]] = []
        if source.checklists_collected:
            files.append(
                SnapshotFile(
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
                task_key = stable_source_key(checklist.task_id)
                page_paths = tuple(
                    Path(
                        "raw",
                        "pages",
                        "checklists",
                        list_key,
                        task_key,
                        f"{number:04d}.json",
                    )
                    for number in range(1, len(checklist.raw_pages) + 1)
                )
                files.extend(
                    SnapshotFile(path, _json_bytes(page))
                    for path, page in zip(page_paths, checklist.raw_pages, strict=True)
                )
                checklist_page_entries.append(
                    {"task_id": checklist.task_id, "files": [str(path) for path in page_paths]}
                )

        display_name = source.raw_list.get("displayName")
        list_entries.append(
            {
                "source_id": list_id,
                "display_name": display_name if isinstance(display_name, str) else None,
                "raw_tasks_file": str(raw_tasks_path),
                "raw_task_page_files": [str(path) for path in task_page_paths],
                "checklists_collected": source.checklists_collected,
                "raw_checklists_file": (
                    str(raw_checklists_path) if source.checklists_collected else None
                ),
                "raw_checklist_page_files": checklist_page_entries,
            }
        )

    manifest: dict[str, Any] = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "tool": {"name": "mstodo-to-ics", "version": __version__},
        "retrieved_at": retrieved_at_text,
        "raw_lists_file": str(raw_lists_path),
        "raw_list_page_files": [str(path) for path in raw_list_page_paths],
        "summary": {
            "lists": len(data.sources),
            "tasks": sum(len(source.raw_tasks) for source in data.sources),
            "checklist_items": sum(
                len(checklist.raw_items)
                for source in data.sources
                for checklist in source.raw_checklists
            ),
        },
        "lists": list_entries,
    }
    files.append(SnapshotFile(Path(SNAPSHOT_MANIFEST_FILENAME), _json_bytes(manifest)))

    paths = tuple(item.relative_path.as_posix().casefold() for item in files)
    if len(paths) != len(set(paths)):
        raise SnapshotError("retrieval snapshot contains colliding output paths")
    return SnapshotPlan(
        retrieved_at=retrieved_at.astimezone(UTC),
        files=tuple(sorted(files, key=lambda item: item.relative_path.as_posix())),
        manifest=manifest,
    )


def _publish(plan: SnapshotPlan, destination: Path) -> None:
    destination = destination.absolute()
    if destination.exists() or destination.is_symlink():
        raise SnapshotError(f"snapshot destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.staging-", dir=destination.parent))
    try:
        for item in plan.files:
            output_path = staging / item.relative_path
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(item.content)
        os.replace(staging, destination)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def write_snapshot(
    destination: Path,
    data: RawExportData,
    *,
    dry_run: bool = False,
    clock: Clock = _utc_now,
) -> SnapshotPlan:
    plan = plan_snapshot(data, retrieved_at=clock())
    if not dry_run:
        _publish(plan, destination)
    return plan


def _mapping(value: Any, *, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SnapshotError(f"{context} must be a JSON object")
    return value


def _mapping_list(value: Any, *, context: str) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, list):
        raise SnapshotError(f"{context} must be a JSON array")
    return tuple(_mapping(item, context=f"{context}[{index}]") for index, item in enumerate(value))


def _safe_file(root: Path, value: Any, *, context: str) -> Path:
    if not isinstance(value, str) or not value:
        raise SnapshotError(f"{context} must be a non-empty relative path")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise SnapshotError(f"{context} is not a safe relative path: {value}")
    path = root / relative
    try:
        path.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as error:
        raise SnapshotError(
            f"{context} is outside or missing from the snapshot: {value}"
        ) from error
    if not path.is_file():
        raise SnapshotError(f"{context} is not a file: {value}")
    return path


def _read_json(path: Path, *, context: str) -> Any:
    try:
        with path.open("rb") as handle:
            return json.load(handle)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SnapshotError(f"could not read {context} from {path}: {error}") from error


def _read_mapping_file(root: Path, value: Any, *, context: str) -> Mapping[str, Any]:
    path = _safe_file(root, value, context=context)
    return _mapping(_read_json(path, context=context), context=context)


def _read_mapping_list_file(
    root: Path, value: Any, *, context: str
) -> tuple[Mapping[str, Any], ...]:
    path = _safe_file(root, value, context=context)
    return _mapping_list(_read_json(path, context=context), context=context)


def _path_list(value: Any, *, context: str) -> Sequence[Any]:
    if not isinstance(value, list):
        raise SnapshotError(f"{context} must be an array")
    return value


def load_snapshot(source: Path) -> RawExportData:
    """Load and validate a previously stored retrieval snapshot without network access."""
    root = source.absolute()
    if not root.is_dir():
        raise SnapshotError(f"snapshot directory does not exist: {root}")
    manifest_path = root / SNAPSHOT_MANIFEST_FILENAME
    manifest = _mapping(_read_json(manifest_path, context="retrieval manifest"), context="manifest")
    if manifest.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
        raise SnapshotError(
            f"unsupported retrieval snapshot schema: {manifest.get('schema_version')!r}"
        )

    raw_lists = _read_mapping_list_file(
        root, manifest.get("raw_lists_file"), context="raw lists file"
    )
    lists_by_id: dict[str, Mapping[str, Any]] = {}
    for raw_list in raw_lists:
        list_id = _required_id(raw_list, context="raw list")
        if list_id in lists_by_id:
            raise SnapshotError(f"duplicate raw list ID: {list_id}")
        lists_by_id[list_id] = raw_list

    raw_list_pages = tuple(
        _read_mapping_file(root, value, context="raw list page")
        for value in _path_list(manifest.get("raw_list_page_files"), context="raw list pages")
    )
    entries = _mapping_list(manifest.get("lists"), context="manifest.lists")
    sources: list[RawListExport] = []
    seen_list_ids: set[str] = set()
    for index, entry in enumerate(entries):
        context = f"manifest.lists[{index}]"
        entry_list_id = entry.get("source_id")
        if not isinstance(entry_list_id, str) or entry_list_id not in lists_by_id:
            raise SnapshotError(f"{context}.source_id does not identify a raw list")
        if entry_list_id in seen_list_ids:
            raise SnapshotError(f"duplicate manifest list ID: {entry_list_id}")
        seen_list_ids.add(entry_list_id)
        raw_tasks = _read_mapping_list_file(
            root, entry.get("raw_tasks_file"), context=f"{context}.raw_tasks_file"
        )
        raw_task_pages = tuple(
            _read_mapping_file(root, value, context=f"{context}.raw_task_page")
            for value in _path_list(
                entry.get("raw_task_page_files"), context=f"{context}.raw_task_page_files"
            )
        )

        collected = entry.get("checklists_collected")
        if not isinstance(collected, bool):
            raise SnapshotError(f"{context}.checklists_collected must be a boolean")
        raw_checklists: list[RawChecklistExport] = []
        if collected:
            checklist_records = _read_mapping_list_file(
                root,
                entry.get("raw_checklists_file"),
                context=f"{context}.raw_checklists_file",
            )
            page_entries = _mapping_list(
                entry.get("raw_checklist_page_files"),
                context=f"{context}.raw_checklist_page_files",
            )
            pages_by_task: dict[str, tuple[Mapping[str, Any], ...]] = {}
            for page_entry in page_entries:
                task_id = page_entry.get("task_id")
                if not isinstance(task_id, str) or task_id in pages_by_task:
                    raise SnapshotError(f"{context} has invalid checklist page task ID")
                pages_by_task[task_id] = tuple(
                    _read_mapping_file(root, value, context=f"{context}.raw_checklist_page")
                    for value in _path_list(
                        page_entry.get("files"), context=f"{context}.checklist page files"
                    )
                )
            for record in checklist_records:
                task_id = record.get("task_id")
                if not isinstance(task_id, str):
                    raise SnapshotError(f"{context} checklist record has no string task_id")
                raw_items = _mapping_list(record.get("items"), context=f"checklist[{task_id}]")
                raw_checklists.append(
                    RawChecklistExport.from_raw(
                        task_id,
                        raw_items,
                        raw_pages=pages_by_task.pop(task_id, ()),
                    )
                )
            if pages_by_task:
                raise SnapshotError(f"{context} has page data for an unknown checklist collection")
        elif entry.get("raw_checklists_file") is not None:
            raise SnapshotError(f"{context} has checklist data marked as not collected")

        sources.append(
            RawListExport.from_raw(
                lists_by_id[entry_list_id],
                raw_tasks,
                raw_task_pages=raw_task_pages,
                raw_checklists=raw_checklists,
                checklists_collected=collected,
            )
        )
    if seen_list_ids != set(lists_by_id):
        raise SnapshotError("retrieval manifest does not reference every raw list")
    return RawExportData(sources=tuple(sources), raw_list_pages=raw_list_pages)
