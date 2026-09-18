# mstodo-to-ics

`mstodo-to-ics` is a Python CLI project for exporting Microsoft To Do lists to
RFC 5545 calendars containing VTODO components. The eventual output is intended
for manual import into Nextcloud Calendar and Tasks.

The project currently supports personal-account Microsoft device-code
authentication, read-only Microsoft Graph retrieval, offline export planning,
and transactional bundle publication. It can retrieve every list/task page,
normalize decoded Graph JSON, produce one validated calendar per list, preserve
raw entities and page envelopes, and generate a migration manifest through a
Typer command line interface.

## Architecture

```text
Typer CLI → MSAL device-code authentication
        |
        v
Microsoft Graph JSON (read-only)
        |
        v
validated read-only pagination
        |
        v
normalized immutable domain values
        |
        v
icalendar Calendar / VTODO components
        |
        v
serialized bytes and parse-back validation
        |
        v
validated ICS files + raw JSON + manifest
```

Graph `dateTimeTimeZone` values retain their original local date-time string and
time-zone identifier. They are not converted through the host machine's local
timezone. UTC instants such as creation and modification timestamps likewise
retain their original source representation.

Bundle publication first creates and validates every output in memory. It then
writes a staging directory beside the destination and atomically renames it, so
conversion failures do not leave a partial export. Existing destinations are not
overwritten.

## Authentication and token caching

Authentication uses MSAL's public-client device-code flow with delegated
`Tasks.Read` permission. The token provider uses an in-memory cache by default,
so it writes no authentication data to disk.

Plain-file persistence is available only when explicitly enabled. It writes the
unencrypted MSAL cache to `.mstodo-to-ics-private-token-cache.json` in the current
working directory. That file contains sensitive token material. The provider
uses an interprocess lock, rejects symlinks and overly broad existing file
permissions, and publishes cache changes atomically with owner-only permissions.
The cache, lock, and possible interrupted-write filenames are ignored by this
repository.

## Command line usage

The export command requires the client ID of a Microsoft public application that
supports personal accounts and device-code authentication. Supply it directly or
through `MSTODO_TO_ICS_CLIENT_ID`:

```bash
mstodo-to-ics export ./todo-export --client-id YOUR_CLIENT_ID

export MSTODO_TO_ICS_CLIENT_ID=YOUR_CLIENT_ID
mstodo-to-ics export ./todo-export
```

The destination must not already exist. Authentication is memory-only unless
plain-file persistence is explicitly enabled:

```bash
mstodo-to-ics export ./todo-export --persist-token-cache
```

Use `--dry-run` to retrieve, convert, and validate without creating the
destination. A dry run still authenticates and sends read-only requests to
Microsoft Graph.

CLI exit codes are `0` for success, `1` for an expected authentication, Graph,
conversion, or filesystem failure, `2` for invalid command usage, and `130` when
an in-progress export is cancelled with `Ctrl+C`.

## Development

Python 3.12 or newer is required.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
pytest
ruff check .
mypy
```

The repositories in `reference/` are read-only prior art and are not runtime
dependencies.
