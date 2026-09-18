# mstodo-to-ics

`mstodo-to-ics` is a Python CLI for retrieving Microsoft To Do data and converting it to
RFC 5545 calendars containing VTODO components. The output is intended for manual import into
Nextcloud Calendar and Tasks.

The workflow deliberately has three separate stages:

```text
auth                           retrieve                         export
device-code login              read-only Microsoft Graph       offline conversion
        |                                |                              |
        v                                v                              v
private token cache            durable raw snapshot            validated ICS bundle
```

Separating retrieval from conversion means the same source snapshot can be exported again after
conversion changes without signing in or querying Microsoft Graph again.

## Installation

Python 3.12 or newer is required.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e .
```

## Configuration

Register a Microsoft public client application that supports personal Microsoft accounts and
device-code authentication. The CLI requests only the delegated `Tasks.Read` permission.

Supply its client ID with `--client-id`, the `MSTODO_TO_ICS_CLIENT_ID` environment variable, or
`.mstodo-to-ics.toml` in the current working directory:

```toml
[auth]
client_id = "YOUR_CLIENT_ID"
```

An alternative config path can be selected with `--config`. Client-ID precedence is the command
option, then the environment variable, then the config file. `auth` and `retrieve` must use the
same client ID.

## Three-step usage

Run all three commands from the same working directory because the private token cache lives
there.

### 1. Authenticate

```bash
mstodo-to-ics auth
```

The command prints Microsoft's device-code instructions and writes the resulting unencrypted MSAL
cache to `.mstodo-to-ics-private-token-cache.json`. Running `auth` is the explicit opt-in to this
persistent private file. The cache and its lock use owner-only permissions, symlinks and unsafe
existing permissions are rejected, and cache replacement is atomic.

### 2. Retrieve a raw snapshot

```bash
mstodo-to-ics retrieve ./todo-snapshot
```

`retrieve` is non-interactive. It uses the cache created by `auth` and fails with instructions to
run `auth` if no usable cached login exists. It retrieves lists, tasks, completed tasks, and each
task's checklist items with validated pagination. The new destination directory is published
transactionally and is never overwritten.

The snapshot contains a schema-versioned `retrieval.json` plus raw entities and original Graph
page envelopes under `raw/`. Unknown fields are retained.

### 3. Export offline

```bash
mstodo-to-ics export ./todo-snapshot ./todo-export
```

`export` does not authenticate or access the network. It loads the stored snapshot, normalizes it,
generates one `.ics` file per list, parses each calendar back for semantic validation, and writes a
manifest. The output also includes the source raw JSON so the migration bundle remains
self-contained.

To rerun conversion after updating the tool, choose a new destination:

```bash
mstodo-to-ics export ./todo-snapshot ./todo-export-v2
```

Both data-producing commands support `--dry-run`. A retrieval dry run still makes read-only Graph
requests but writes no snapshot. An export dry run is entirely offline and writes no bundle.

## Conversion behavior

Graph `dateTimeTimeZone` values retain their original local date-time string and timezone
identifier rather than passing through the machine's local timezone.

Checklist items remain on their parent task. They are preserved structurally in raw JSON and are
rendered in the parent VTODO `DESCRIPTION` after the original notes:

```text
Checklist:
- [ ] Buy paint
- [x] Measure wall
```

No child VTODO components are created. Recurrence and reminders are preserved in raw JSON,
counted in the manifest, and reported as warnings; v1 deliberately does not emit `RRULE` or
`VALARM`.

Final bundle publication first creates and validates every output in memory, writes a staging
directory beside the destination, and atomically renames it. Existing destinations are not
overwritten.

CLI exit codes are `0` for success, `1` for an expected authentication, Graph, snapshot,
conversion, or filesystem failure, `2` for invalid command usage, and `130` for cancellation with
`Ctrl+C`.

## Development

```bash
python -m pip install -e '.[dev]'
pytest
ruff check .
ruff format --check .
mypy src tests
```

The repositories in `reference/` are read-only prior art and are not runtime dependencies.
