# mstodo-to-ics

`mstodo-to-ics` is a Python CLI project for exporting Microsoft To Do lists to
RFC 5545 calendars containing VTODO components. The eventual output is intended
for manual import into Nextcloud Calendar and Tasks.

The project currently supports personal-account Microsoft device-code
authentication, read-only Microsoft Graph retrieval, offline export planning,
and transactional bundle publication. It can retrieve every list/task page,
normalize decoded Graph JSON, produce one validated calendar per list, preserve
raw entities and page envelopes, and generate a migration manifest. CLI wiring
for the online export flow is not implemented yet.

## Architecture

```text
Microsoft Graph JSON
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
