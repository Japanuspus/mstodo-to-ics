from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from mstodo_to_ics import __version__, cli
from mstodo_to_ics.export import (
    ExportPlan,
    RawChecklistExport,
    RawExportData,
    RawListExport,
)
from mstodo_to_ics.graph import AccessTokenProvider, GraphError
from mstodo_to_ics.snapshot import SnapshotPlan, load_snapshot, write_snapshot

FIXED_NOW = datetime(2026, 9, 18, 12, 30, tzinfo=UTC)
runner = CliRunner()


class StubTokenProvider:
    def __init__(self) -> None:
        self.calls: list[bool] = []

    def get_token(self, *, force_refresh: bool = False) -> str:
        self.calls.append(force_refresh)
        return "token"


def _task() -> dict[str, Any]:
    return {
        "id": "task-1",
        "title": "Export me",
        "body": {"content": "Notes", "contentType": "text"},
        "status": "notStarted",
        "importance": "normal",
        "createdDateTime": "2026-09-17T09:00:00Z",
        "lastModifiedDateTime": "2026-09-18T12:00:00Z",
        "completedDateTime": None,
        "dueDateTime": None,
        "startDateTime": None,
        "categories": [],
    }


def _raw_data() -> RawExportData:
    return RawExportData.from_sources(
        [
            RawListExport.from_raw(
                {"id": "list-1", "displayName": "CLI Test"},
                [_task()],
                raw_checklists=[
                    RawChecklistExport.from_raw(
                        "task-1",
                        [{"id": "check-1", "displayName": "Check me", "isChecked": False}],
                    )
                ],
                checklists_collected=True,
            )
        ]
    )


def _snapshot_plan() -> SnapshotPlan:
    return SnapshotPlan(
        retrieved_at=FIXED_NOW,
        files=(),
        manifest={"summary": {"lists": 2, "tasks": 3, "checklist_items": 4}},
    )


def _export_plan(*, warnings: list[str] | None = None) -> ExportPlan:
    return ExportPlan(
        exported_at=FIXED_NOW,
        files=(),
        manifest={"summary": {"lists": 2, "tasks": 3}, "warnings": warnings or []},
    )


def test_execute_auth_is_persistent_and_interactive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    provider = StubTokenProvider()
    calls: list[tuple[str, bool, bool, Callable[[str], None]]] = []
    messages: list[str] = []

    def factory(
        client_id: str,
        *,
        persist_cache: bool,
        allow_device_code: bool,
        device_code_callback: Callable[[str], None],
    ) -> AccessTokenProvider:
        calls.append((client_id, persist_cache, allow_device_code, device_code_callback))
        return provider

    cache_path = cli.execute_auth(
        client_id="client-id",
        device_code_callback=messages.append,
        token_provider_factory=factory,
    )

    assert calls == [("client-id", True, True, messages.append)]
    assert provider.calls == [False]
    assert cache_path == tmp_path / ".mstodo-to-ics-private-token-cache.json"


@pytest.mark.parametrize("dry_run", [False, True])
def test_execute_retrieve_requires_cache_and_writes_snapshot(
    tmp_path: Path,
    dry_run: bool,
) -> None:
    destination = tmp_path / "snapshot"
    provider = StubTokenProvider()
    factory_calls: list[tuple[bool, bool]] = []
    fetch_calls: list[AccessTokenProvider] = []

    def factory(
        client_id: str,
        *,
        persist_cache: bool,
        allow_device_code: bool,
        device_code_callback: Callable[[str], None],
    ) -> AccessTokenProvider:
        assert client_id == "client-id"
        factory_calls.append((persist_cache, allow_device_code))
        return provider

    def fetch(received: AccessTokenProvider) -> RawExportData:
        fetch_calls.append(received)
        return _raw_data()

    plan = cli.execute_retrieve(
        destination,
        client_id="client-id",
        dry_run=dry_run,
        token_provider_factory=factory,
        data_fetcher=fetch,
    )

    assert factory_calls == [(True, False)]
    assert fetch_calls == [provider]
    assert plan.manifest["summary"] == {"lists": 1, "tasks": 1, "checklist_items": 1}
    assert destination.exists() is not dry_run


def test_execute_export_is_offline_and_repeatable(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot"
    write_snapshot(snapshot, _raw_data(), clock=lambda: FIXED_NOW)
    first = tmp_path / "export-v1"
    second = tmp_path / "export-v2"

    cli.execute_export(snapshot, first, dry_run=False)
    cli.execute_export(snapshot, second, dry_run=False)

    assert (first / "CLI Test.ics").read_bytes() == (second / "CLI Test.ics").read_bytes()
    assert load_snapshot(snapshot).sources[0].raw_checklists[0].task_id == "task-1"


def test_auth_command_uses_config_and_reports_private_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".mstodo-to-ics.toml").write_text(
        '[auth]\nclient_id = "configured-client-id"\n', encoding="utf-8"
    )
    received: list[str] = []

    def fake_auth(*, client_id: str, device_code_callback: Callable[[str], None]) -> Path:
        received.append(client_id)
        device_code_callback("Enter code ABCD-EFGH.")
        return tmp_path / ".mstodo-to-ics-private-token-cache.json"

    monkeypatch.setattr(cli, "execute_auth", fake_auth)
    result = runner.invoke(cli.app, ["auth"])

    assert result.exit_code == 0
    assert received == ["configured-client-id"]
    assert "Authentication complete." in result.stdout
    assert "private-token-cache.json" in result.stdout
    assert "Enter code ABCD-EFGH." in result.stderr


def test_retrieve_command_uses_client_id_and_reports_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Path, str, bool]] = []

    def fake_retrieve(destination: Path, *, client_id: str, dry_run: bool) -> SnapshotPlan:
        calls.append((destination, client_id, dry_run))
        return _snapshot_plan()

    monkeypatch.setattr(cli, "execute_retrieve", fake_retrieve)
    destination = tmp_path / "snapshot"
    result = runner.invoke(
        cli.app,
        ["retrieve", str(destination), "--dry-run"],
        env={cli.CLIENT_ID_ENVIRONMENT_VARIABLE: "environment-client-id"},
    )

    assert result.exit_code == 0
    assert calls == [(destination, "environment-client-id", True)]
    assert "3 task(s), 4 checklist item(s)" in result.stdout


def test_export_command_is_offline_and_prints_warnings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Path, Path, bool]] = []

    def fake_export(source: Path, destination: Path, *, dry_run: bool) -> ExportPlan:
        calls.append((source, destination, dry_run))
        return _export_plan(warnings=["Recurrence preserved but not converted."])

    monkeypatch.setattr(cli, "execute_export", fake_export)
    source = tmp_path / "snapshot"
    destination = tmp_path / "export"
    result = runner.invoke(cli.app, ["export", str(source), str(destination)])

    assert result.exit_code == 0
    assert calls == [(source, destination, False)]
    assert "Exported 2 list(s) and 3 task(s)." in result.stdout
    assert "Warning: Recurrence preserved but not converted." in result.stderr


def test_export_rejects_authentication_options(tmp_path: Path) -> None:
    result = runner.invoke(
        cli.app,
        ["export", str(tmp_path / "source"), str(tmp_path / "out"), "--client-id", "x"],
    )
    assert result.exit_code == 2
    assert "No such option" in result.stderr


def test_expected_operational_error_is_concise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_retrieve(*args: object, **kwargs: object) -> SnapshotPlan:
        raise GraphError("Graph request failed safely")

    monkeypatch.setattr(cli, "execute_retrieve", fail_retrieve)
    result = runner.invoke(
        cli.app,
        ["retrieve", str(tmp_path / "failed"), "--client-id", "client-id"],
    )
    assert result.exit_code == 1
    assert result.stderr == "Error: Graph request failed safely\n"


def test_keyboard_interrupt_exits_with_130(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def interrupt(*args: object, **kwargs: object) -> ExportPlan:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "execute_export", interrupt)
    result = runner.invoke(cli.app, ["export", str(tmp_path / "snapshot"), str(tmp_path / "out")])
    assert result.exit_code == 130
    assert result.stderr == "Cancelled.\n"


def test_missing_client_id_is_a_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli.app, ["retrieve", str(tmp_path / "snapshot")])
    assert result.exit_code == 2
    assert "provide --client-id" in result.stderr


def test_help_and_version() -> None:
    help_result = runner.invoke(cli.app, ["--help"])
    version_result = runner.invoke(cli.app, ["--version"])

    assert help_result.exit_code == 0
    assert all(command in help_result.stdout for command in ("auth", "retrieve", "export"))
    assert version_result.exit_code == 0
    assert version_result.stdout == f"mstodo-to-ics {__version__}\n"
