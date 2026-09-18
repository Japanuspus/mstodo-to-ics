from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from mstodo_to_ics import __version__, cli
from mstodo_to_ics.export import ExportInput, ExportPlan, RawListExport
from mstodo_to_ics.graph import AccessTokenProvider, GraphError

FIXED_NOW = datetime(2026, 9, 18, 12, 30, tzinfo=UTC)
runner = CliRunner()


class StubTokenProvider:
    def get_token(self, *, force_refresh: bool = False) -> str:
        return "unused-offline-token"


def _raw_source() -> RawListExport:
    return RawListExport.from_raw(
        {"id": "list-1", "displayName": "CLI Test"},
        [
            {
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
        ],
    )


def _display_plan(*, warnings: list[str] | None = None) -> ExportPlan:
    return ExportPlan(
        exported_at=FIXED_NOW,
        files=(),
        manifest={
            "summary": {"lists": 2, "tasks": 3},
            "warnings": warnings or [],
        },
    )


@pytest.mark.parametrize("dry_run", [False, True])
def test_execute_export_wires_dependencies_and_real_bundle_exporter(
    tmp_path: Path,
    dry_run: bool,
) -> None:
    destination = tmp_path / "migration"
    provider = StubTokenProvider()
    factory_calls: list[tuple[str, bool, Callable[[str], None]]] = []
    fetch_calls: list[AccessTokenProvider] = []
    messages: list[str] = []

    def token_provider_factory(
        client_id: str,
        *,
        persist_cache: bool,
        device_code_callback: Callable[[str], None],
    ) -> AccessTokenProvider:
        factory_calls.append((client_id, persist_cache, device_code_callback))
        return provider

    def data_fetcher(received_provider: AccessTokenProvider) -> ExportInput:
        fetch_calls.append(received_provider)
        return (_raw_source(),)

    plan = cli.execute_export(
        destination,
        client_id="client-id",
        persist_token_cache=True,
        dry_run=dry_run,
        device_code_callback=messages.append,
        token_provider_factory=token_provider_factory,
        data_fetcher=data_fetcher,
    )

    assert factory_calls == [("client-id", True, messages.append)]
    assert fetch_calls == [provider]
    assert plan.manifest["summary"]["lists"] == 1
    assert plan.manifest["summary"]["tasks"] == 1
    assert destination.exists() is not dry_run
    if not dry_run:
        assert (destination / "manifest.json").is_file()
        assert (destination / "CLI Test.ics").is_file()


def test_export_command_forwards_options_and_prints_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "migration"
    calls: list[dict[str, Any]] = []

    def fake_execute_export(
        received_destination: Path,
        *,
        client_id: str,
        persist_token_cache: bool,
        dry_run: bool,
        device_code_callback: Callable[[str], None],
    ) -> ExportPlan:
        calls.append(
            {
                "destination": received_destination,
                "client_id": client_id,
                "persist_token_cache": persist_token_cache,
                "dry_run": dry_run,
            }
        )
        device_code_callback("Use code ABCD-EFGH to sign in.")
        return _display_plan(warnings=["A feature was preserved but not converted."])

    monkeypatch.setattr(cli, "execute_export", fake_execute_export)

    result = runner.invoke(
        cli.app,
        [
            "export",
            str(destination),
            "--client-id",
            "client-id",
            "--persist-token-cache",
        ],
    )

    assert result.exit_code == 0
    assert calls == [
        {
            "destination": destination,
            "client_id": "client-id",
            "persist_token_cache": True,
            "dry_run": False,
        }
    ]
    assert f"Export complete: {destination.absolute()}" in result.stdout
    assert "Exported 2 list(s) and 3 task(s)." in result.stdout
    assert "Token cache: private plaintext file." in result.stdout
    assert "Use code ABCD-EFGH to sign in." in result.stderr
    assert "Warning: A feature was preserved but not converted." in result.stderr


def test_export_command_accepts_client_id_from_environment_and_reports_dry_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "dry-run"
    calls: list[tuple[str, bool]] = []

    def fake_execute_export(
        received_destination: Path,
        *,
        client_id: str,
        persist_token_cache: bool,
        dry_run: bool,
        device_code_callback: Callable[[str], None],
    ) -> ExportPlan:
        assert received_destination == destination
        assert persist_token_cache is False
        calls.append((client_id, dry_run))
        return _display_plan()

    monkeypatch.setattr(cli, "execute_export", fake_execute_export)

    result = runner.invoke(
        cli.app,
        ["export", str(destination), "--dry-run"],
        env={cli.CLIENT_ID_ENVIRONMENT_VARIABLE: "environment-client-id"},
    )

    assert result.exit_code == 0
    assert calls == [("environment-client-id", True)]
    assert "Dry run succeeded: 2 list(s), 3 task(s); no files were written." in result.stdout
    assert f"Proposed destination: {destination.absolute()}" in result.stdout
    assert "Token cache: memory only." in result.stdout


def test_expected_operational_error_is_concise(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_export(*args: object, **kwargs: object) -> ExportPlan:
        raise GraphError("Graph request failed safely")

    monkeypatch.setattr(cli, "execute_export", fail_export)

    result = runner.invoke(
        cli.app,
        ["export", str(tmp_path / "failed"), "--client-id", "client-id"],
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == "Error: Graph request failed safely\n"
    assert "Traceback" not in result.stderr


def test_keyboard_interrupt_exits_with_130(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def interrupt_export(*args: object, **kwargs: object) -> ExportPlan:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "execute_export", interrupt_export)

    result = runner.invoke(
        cli.app,
        ["export", str(tmp_path / "cancelled"), "--client-id", "client-id"],
    )

    assert result.exit_code == 130
    assert result.stderr == "Cancelled.\n"


def test_missing_client_id_is_a_usage_error(tmp_path: Path) -> None:
    result = runner.invoke(cli.app, ["export", str(tmp_path / "missing-client")])

    assert result.exit_code == 2
    assert "Missing option '--client-id'" in result.stderr


def test_help_and_version() -> None:
    help_result = runner.invoke(cli.app, ["--help"])
    version_result = runner.invoke(cli.app, ["--version"])

    assert help_result.exit_code == 0
    assert "export" in help_result.stdout
    assert "--version" in help_result.stdout
    assert version_result.exit_code == 0
    assert version_result.stdout == f"mstodo-to-ics {__version__}\n"
