# mstodo-to-ics

`mstodo-to-ics` is a Python CLI project for exporting Microsoft To Do lists to
RFC 5545 calendars containing VTODO components. The eventual output is intended
for manual import into Nextcloud Calendar and Tasks.

The project currently supports read-only Microsoft Graph retrieval behind an
injected token provider, offline export planning, and transactional bundle
publication. It can retrieve every list/task page, normalize decoded Graph JSON,
produce one validated calendar per list, preserve raw entities and page envelopes,
and generate a migration manifest. Microsoft device-code authentication is not
implemented yet.

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
