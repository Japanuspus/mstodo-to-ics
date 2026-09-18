from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from mstodo_to_ics.export import RawChecklistExport, RawExportData, RawListExport
from mstodo_to_ics.snapshot import SnapshotError, load_snapshot, write_snapshot

FIXED_NOW = datetime(2026, 9, 18, 12, 30, tzinfo=UTC)


def _data() -> RawExportData:
    task: dict[str, Any] = {
        "id": "task/1",
        "title": "Task",
        "body": {"content": "Notes", "contentType": "text"},
        "status": "notStarted",
        "importance": "normal",
        "createdDateTime": "2026-09-17T08:00:00Z",
        "lastModifiedDateTime": "2026-09-17T09:00:00Z",
        "completedDateTime": None,
        "dueDateTime": None,
        "startDateTime": None,
        "categories": [],
        "unknown": {"preserved": True},
    }
    source = RawListExport.from_raw(
        {"id": "list/1", "displayName": "List", "unknown": "preserved"},
        [task],
        raw_task_pages=[{"value": [task], "pageUnknown": 1}],
        raw_checklists=[
            RawChecklistExport.from_raw(
                "task/1",
                [{"id": "check/1", "displayName": "Punkt æ", "isChecked": True}],
                raw_pages=[{"value": [{"id": "check/1"}], "pageUnknown": 2}],
            )
        ],
        checklists_collected=True,
    )
    return RawExportData(
        sources=(source,),
        raw_list_pages=({"value": [source.raw_list], "pageUnknown": 3},),
    )


def test_snapshot_round_trip_preserves_entities_and_page_envelopes(tmp_path: Path) -> None:
    destination = tmp_path / "snapshot"
    original = _data()

    plan = write_snapshot(destination, original, clock=lambda: FIXED_NOW)
    loaded = load_snapshot(destination)

    assert loaded == original
    assert plan.manifest["retrieved_at"] == "2026-09-18T12:30:00Z"
    assert plan.manifest["summary"] == {"lists": 1, "tasks": 1, "checklist_items": 1}
    paths = {path.as_posix() for path in plan.relative_paths}
    assert "retrieval.json" in paths
    assert any(path.startswith("raw/checklists/") for path in paths)
    assert any(path.startswith("raw/pages/checklists/") for path in paths)


def test_snapshot_dry_run_and_existing_destination_are_safe(tmp_path: Path) -> None:
    destination = tmp_path / "snapshot"

    write_snapshot(destination, _data(), dry_run=True, clock=lambda: FIXED_NOW)
    assert not destination.exists()

    destination.mkdir()
    sentinel = destination / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    with pytest.raises(SnapshotError, match="already exists"):
        write_snapshot(destination, _data(), clock=lambda: FIXED_NOW)
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_snapshot_loader_rejects_paths_outside_snapshot(tmp_path: Path) -> None:
    destination = tmp_path / "snapshot"
    write_snapshot(destination, _data(), clock=lambda: FIXED_NOW)
    manifest_path = destination / "retrieval.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["raw_lists_file"] = "../outside.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(SnapshotError, match="not a safe relative path"):
        load_snapshot(destination)


def test_snapshot_loader_requires_supported_manifest(tmp_path: Path) -> None:
    destination = tmp_path / "snapshot"
    destination.mkdir()
    (destination / "retrieval.json").write_text(
        json.dumps({"schema_version": 999}), encoding="utf-8"
    )

    with pytest.raises(SnapshotError, match="unsupported retrieval snapshot schema"):
        load_snapshot(destination)
