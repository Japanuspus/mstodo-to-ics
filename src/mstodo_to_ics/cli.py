"""Typer command line interface for the Microsoft To Do exporter."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Annotated, Any, Protocol

import typer

from . import __version__
from .auth import AuthenticationError, MsalTokenProvider
from .config import ConfigError, load_config
from .export import ExportError, ExportInput, ExportPlan, export_bundle
from .graph import AccessTokenProvider, GraphClient, GraphError
from .normalize import NormalizationError
from .validate import CalendarValidationError

CLIENT_ID_ENVIRONMENT_VARIABLE = "MSTODO_TO_ICS_CLIENT_ID"
OPERATIONAL_ERRORS = (
    AuthenticationError,
    GraphError,
    ExportError,
    NormalizationError,
    CalendarValidationError,
    OSError,
)

DeviceCodeCallback = Callable[[str], None]


class TokenProviderFactory(Protocol):
    def __call__(
        self,
        client_id: str,
        *,
        persist_cache: bool,
        device_code_callback: DeviceCodeCallback,
    ) -> AccessTokenProvider: ...


type DataFetcher = Callable[[AccessTokenProvider], ExportInput]


class BundleExporter(Protocol):
    def __call__(
        self,
        destination: Path,
        sources: ExportInput,
        *,
        dry_run: bool = False,
    ) -> ExportPlan: ...


def _new_token_provider(
    client_id: str,
    *,
    persist_cache: bool,
    device_code_callback: DeviceCodeCallback,
) -> AccessTokenProvider:
    return MsalTokenProvider(
        client_id,
        persist_cache=persist_cache,
        device_code_callback=device_code_callback,
    )


def _fetch_graph_data(provider: AccessTokenProvider) -> ExportInput:
    with GraphClient(provider) as graph:
        return graph.fetch_export_data()


def execute_export(
    destination: Path,
    *,
    client_id: str,
    persist_token_cache: bool,
    dry_run: bool,
    device_code_callback: DeviceCodeCallback,
    token_provider_factory: TokenProviderFactory = _new_token_provider,
    data_fetcher: DataFetcher = _fetch_graph_data,
    bundle_exporter: BundleExporter = export_bundle,
) -> ExportPlan:
    """Authenticate, retrieve all Graph data, and build the export bundle."""
    provider = token_provider_factory(
        client_id,
        persist_cache=persist_token_cache,
        device_code_callback=device_code_callback,
    )
    sources = data_fetcher(provider)
    return bundle_exporter(destination, sources, dry_run=dry_run)


def _summary_integer(summary: Mapping[str, Any], key: str) -> int:
    value = summary.get(key)
    return value if isinstance(value, int) else 0


def _print_success(
    plan: ExportPlan,
    destination: Path,
    *,
    dry_run: bool,
    persist_token_cache: bool,
) -> None:
    raw_summary = plan.manifest.get("summary")
    summary = raw_summary if isinstance(raw_summary, Mapping) else {}
    list_count = _summary_integer(summary, "lists")
    task_count = _summary_integer(summary, "tasks")

    if dry_run:
        typer.echo(
            f"Dry run succeeded: {list_count} list(s), {task_count} task(s); no files were written."
        )
        typer.echo(f"Proposed destination: {destination.absolute()}")
    else:
        typer.echo(f"Export complete: {destination.absolute()}")
        typer.echo(f"Exported {list_count} list(s) and {task_count} task(s).")

    cache_mode = "private plaintext file" if persist_token_cache else "memory only"
    typer.echo(f"Token cache: {cache_mode}.")

    raw_warnings = plan.manifest.get("warnings")
    if isinstance(raw_warnings, list):
        for warning in raw_warnings:
            if isinstance(warning, str):
                typer.echo(f"Warning: {warning}", err=True)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"mstodo-to-ics {__version__}")
        raise typer.Exit()


def _resolve_client_id(client_id: str | None, config_path: Path | None) -> str:
    supplied_client_id = client_id.strip() if client_id is not None else ""
    if supplied_client_id and config_path is None:
        return supplied_client_id

    try:
        config = load_config(config_path)
    except ConfigError as error:
        raise typer.BadParameter(str(error), param_hint="--config") from error

    if supplied_client_id:
        return supplied_client_id
    if config.client_id is not None:
        return config.client_id
    raise typer.BadParameter(
        "provide --client-id, set MSTODO_TO_ICS_CLIENT_ID, or configure [auth].client_id",
        param_hint="--client-id",
    )


app = typer.Typer(
    name="mstodo-to-ics",
    help="Export Microsoft To Do lists as validated RFC 5545 VTODO calendars.",
    no_args_is_help=True,
    add_completion=False,
)


@app.callback()
def root(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Show the version and exit.",
        ),
    ] = False,
) -> None:
    """Export Microsoft To Do lists for manual import into Nextcloud."""


@app.command("export")
def export_command(
    destination: Annotated[
        Path,
        typer.Argument(help="New directory in which to create the migration bundle."),
    ],
    client_id: Annotated[
        str | None,
        typer.Option(
            "--client-id",
            envvar=CLIENT_ID_ENVIRONMENT_VARIABLE,
            help="Microsoft public application client ID; overrides the config file.",
        ),
    ] = None,
    config_path: Annotated[
        Path | None,
        typer.Option(
            "--config",
            help="TOML config path; defaults to .mstodo-to-ics.toml in the working directory.",
        ),
    ] = None,
    persist_token_cache: Annotated[
        bool,
        typer.Option(
            "--persist-token-cache/--no-persist-token-cache",
            help="Persist the unencrypted private MSAL cache in the working directory.",
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Retrieve and validate everything without writing the destination.",
        ),
    ] = False,
) -> None:
    """Authenticate, retrieve every To Do list and task, and create an export."""

    def show_device_code(message: str) -> None:
        typer.echo(message, err=True)

    resolved_client_id = _resolve_client_id(client_id, config_path)

    try:
        plan = execute_export(
            destination,
            client_id=resolved_client_id,
            persist_token_cache=persist_token_cache,
            dry_run=dry_run,
            device_code_callback=show_device_code,
        )
    except KeyboardInterrupt as error:
        typer.echo("Cancelled.", err=True)
        raise typer.Exit(code=130) from error
    except OPERATIONAL_ERRORS as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from error

    _print_success(
        plan,
        destination,
        dry_run=dry_run,
        persist_token_cache=persist_token_cache,
    )


def main() -> None:
    """Run the Typer application."""
    app()


if __name__ == "__main__":
    main()
