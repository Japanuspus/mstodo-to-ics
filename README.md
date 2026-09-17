# mstodo-to-ics

`mstodo-to-ics` is a Python CLI project for exporting Microsoft To Do lists to
RFC 5545 calendars containing VTODO components. The eventual output is intended
for manual import into Nextcloud Calendar and Tasks.

The project is currently at its first offline milestone. It can normalize fixture
JSON, generate one calendar from one list, and validate the generated calendar by
parsing it again. Microsoft authentication and network access are not implemented
yet.

## Architecture

```text
Microsoft Graph JSON
        |
        v
normalized immutable domain values
        |
        v
icalendar Calendar / VTODO components
        |
        v
serialized bytes and parse-back validation
```

Graph `dateTimeTimeZone` values retain their original local date-time string and
time-zone identifier. They are not converted through the host machine's local
timezone. UTC instants such as creation and modification timestamps likewise
retain their original source representation.

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

