"""Typer commands for the explicit auth, retrieve, and offline export stages."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Annotated, Any, Protocol

import typer

from . import __version__
from .auth import PRIVATE_TOKEN_CACHE_FILENAME, AuthenticationError, MsalTokenProvider
from .config import ConfigError, load_config
from .export import ExportError, ExportInput, ExportPlan, RawExportData, export_bundle
from .graph import AccessTokenProvider, GraphClient, GraphError
from .normalize import NormalizationError
from .snapshot import SnapshotError, SnapshotPlan, load_snapshot, write_snapshot
from .validate import CalendarValidationError

CLIENT_ID_ENVIRONMENT_VARIABLE = "MSTODO_TO_ICS_CLIENT_ID"
OPERATIONAL_ERRORS = (
    AuthenticationError,
    GraphError,
    SnapshotError,
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
        allow_device_code: bool,
        device_code_callback: DeviceCodeCallback,
    ) -> AccessTokenProvider: ...


type DataFetcher = Callable[[AccessTokenProvider], RawExportData]
type SnapshotLoader = Callable[[Path], RawExportData]


class SnapshotWriter(Protocol):
    def __call__(
        self,
        destination: Path,
        data: RawExportData,
        *,
        dry_run: bool = False,
    ) -> SnapshotPlan: ...


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
    allow_device_code: bool,
    device_code_callback: DeviceCodeCallback,
) -> AccessTokenProvider:
    return MsalTokenProvider(
        client_id,
        persist_cache=persist_cache,
        allow_device_code=allow_device_code,
        device_code_callback=device_code_callback,
    )


def _fetch_graph_data(provider: AccessTokenProvider) -> RawExportData:
    with GraphClient(provider) as graph:
        return graph.fetch_export_data()


def execute_auth(
    *,
    client_id: str,
    device_code_callback: DeviceCodeCallback,
    token_provider_factory: TokenProviderFactory = _new_token_provider,
) -> Path:
    """Interactively authenticate and persist the private cache in the CWD."""
    provider = token_provider_factory(
        client_id,
        persist_cache=True,
        allow_device_code=True,
        device_code_callback=device_code_callback,
    )
    provider.get_token()
    return (Path.cwd() / PRIVATE_TOKEN_CACHE_FILENAME).absolute()


def execute_retrieve(
    destination: Path,
    *,
    client_id: str,
    dry_run: bool,
    token_provider_factory: TokenProviderFactory = _new_token_provider,
    data_fetcher: DataFetcher = _fetch_graph_data,
    snapshot_writer: SnapshotWriter = write_snapshot,
) -> SnapshotPlan:
    """Use the persisted login to retrieve Graph data into a durable snapshot."""
    provider = token_provider_factory(
        client_id,
        persist_cache=True,
        allow_device_code=False,
        device_code_callback=lambda _: None,
    )
    data = data_fetcher(provider)
    return snapshot_writer(destination, data, dry_run=dry_run)


def execute_export(
    source: Path,
    destination: Path,
    *,
    dry_run: bool,
    snapshot_loader: SnapshotLoader = load_snapshot,
    bundle_exporter: BundleExporter = export_bundle,
) -> ExportPlan:
    """Load a raw snapshot and convert it without authentication or network access."""
    data = snapshot_loader(source)
    return bundle_exporter(destination, data, dry_run=dry_run)


def _summary_integer(manifest: Mapping[str, Any], key: str) -> int:
    raw_summary = manifest.get("summary")
    summary = raw_summary if isinstance(raw_summary, Mapping) else {}
    value = summary.get(key)
    return value if isinstance(value, int) else 0


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


def _show_error(error: BaseException) -> None:
    typer.echo(f"Error: {error}", err=True)


app = typer.Typer(
    name="mstodo-to-ics",
    help="Retrieve Microsoft To Do data and export validated RFC 5545 VTODO calendars.",
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
    """Run the explicit authentication, retrieval, and offline export stages."""


@app.command("auth")
def auth_command(
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
) -> None:
    """Sign in interactively and create the private token cache in the working directory."""

    def show_device_code(message: str) -> None:
        typer.echo(message, err=True)

    resolved_client_id = _resolve_client_id(client_id, config_path)
    try:
        cache_path = execute_auth(
            client_id=resolved_client_id,
            device_code_callback=show_device_code,
        )
    except KeyboardInterrupt as error:
        typer.echo("Cancelled.", err=True)
        raise typer.Exit(code=130) from error
    except OPERATIONAL_ERRORS as error:
        _show_error(error)
        raise typer.Exit(code=1) from error
    typer.echo("Authentication complete.")
    typer.echo(f"Private plaintext token cache: {cache_path}")


@app.command("retrieve")
def retrieve_command(
    destination: Annotated[
        Path,
        typer.Argument(help="New directory in which to store the raw retrieval snapshot."),
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
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Retrieve and validate without writing the snapshot."),
    ] = False,
) -> None:
    """Retrieve all source data using the private cache created by auth."""
    resolved_client_id = _resolve_client_id(client_id, config_path)
    try:
        plan = execute_retrieve(destination, client_id=resolved_client_id, dry_run=dry_run)
    except KeyboardInterrupt as error:
        typer.echo("Cancelled.", err=True)
        raise typer.Exit(code=130) from error
    except OPERATIONAL_ERRORS as error:
        _show_error(error)
        raise typer.Exit(code=1) from error
    lists = _summary_integer(plan.manifest, "lists")
    tasks = _summary_integer(plan.manifest, "tasks")
    checklists = _summary_integer(plan.manifest, "checklist_items")
    if dry_run:
        typer.echo(
            f"Retrieval dry run succeeded: {lists} list(s), {tasks} task(s), "
            f"{checklists} checklist item(s); no files were written."
        )
    else:
        typer.echo(f"Retrieval complete: {destination.absolute()}")
        typer.echo(f"Stored {lists} list(s), {tasks} task(s), and {checklists} checklist item(s).")


@app.command("export")
def export_command(
    source: Annotated[
        Path,
        typer.Argument(help="Retrieval snapshot directory created by the retrieve command."),
    ],
    destination: Annotated[
        Path,
        typer.Argument(help="New directory in which to create the migration bundle."),
    ],
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Convert and validate without writing the destination."),
    ] = False,
) -> None:
    """Convert a stored snapshot into ICS files without authentication or network access."""
    try:
        plan = execute_export(source, destination, dry_run=dry_run)
    except KeyboardInterrupt as error:
        typer.echo("Cancelled.", err=True)
        raise typer.Exit(code=130) from error
    except OPERATIONAL_ERRORS as error:
        _show_error(error)
        raise typer.Exit(code=1) from error
    lists = _summary_integer(plan.manifest, "lists")
    tasks = _summary_integer(plan.manifest, "tasks")
    if dry_run:
        typer.echo(
            f"Export dry run succeeded: {lists} list(s), {tasks} task(s); no files were written."
        )
    else:
        typer.echo(f"Export complete: {destination.absolute()}")
        typer.echo(f"Exported {lists} list(s) and {tasks} task(s).")
    raw_warnings = plan.manifest.get("warnings")
    if isinstance(raw_warnings, list):
        for warning in raw_warnings:
            if isinstance(warning, str):
                typer.echo(f"Warning: {warning}", err=True)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
