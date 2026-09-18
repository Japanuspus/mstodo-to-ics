# Codex Brief: Microsoft To Do → Nextcloud Tasks migration exporter (Python)

## Goal

Build a small standalone **Python CLI tool** that exports Microsoft To Do data to **one RFC 5545 `.ics` file per Microsoft To Do list**, suitable for **manual import into Nextcloud Calendar / Tasks**.

The implementation must be a new Python project. Existing repositories under `reference/` are prior art only and must not become the project architecture.

Primary flow:

```text
mstodo-to-ics auth
    │ device-code login; private cache in the working directory
    ▼
mstodo-to-ics retrieve ./todo-snapshot
    │ Microsoft Graph, read-only
    ▼
durable raw source snapshot
    │
    │ offline and repeatable
    ▼
mstodo-to-ics export ./todo-snapshot ./todo-export
    ├── one .ics file per list
    ├── preserved raw source JSON
    └── manifest / validation report
    │
    ▼
manual import into Nextcloud Calendar
    │
    ▼
Nextcloud Tasks
```

## Language and implementation constraint

The product must be implemented in **Python**.

Preferred baseline:

- Python 3.12+
- standard `pyproject.toml`
- `src/` layout
- pytest for tests
- type annotations throughout
- no TypeScript implementation
- no Rust implementation
- no Node.js runtime dependency

Use current maintained Python packages where appropriate rather than mechanically porting TypeScript or Go code.

Likely dependencies to evaluate:

- `azure-identity`
- `msgraph-sdk`, or direct Graph HTTP if simpler
- `icalendar`
- `httpx`
- `typer` or `argparse`
- `pytest`

Do not add dependencies merely because they appear in the reference projects.

## Repository layout

Expected layout:

```text
mstodo-to-ics/
├── CODEX_MSTODO_TO_NEXTCLOUD_PLAN_PYTHON.md
├── pyproject.toml
├── README.md
├── src/
│   └── mstodo_to_ics/
│       ├── __init__.py
│       ├── auth.py
│       ├── graph.py
│       ├── models.py
│       ├── convert.py
│       ├── ics.py
│       ├── snapshot.py
│       ├── export.py
│       └── cli.py
├── tests/
│   ├── fixtures/
│   └── ...
└── reference/
    ├── mcp-microsoft-todo/
    └── Microsoft-To-Do-Export/
```

Add:

```gitignore
reference/
```

The repos under `reference/` are **read-only prior art** and should not be committed as part of the product.

## Reference repositories

### `reference/mcp-microsoft-todo`

Upstream:

```text
https://github.com/MAG-Cie/mcp-microsoft-todo
```

This is a **TypeScript** project.

Use it only as reference for:

- Microsoft To Do Graph behavior
- list/task/checklist retrieval
- VTODO generation
- recurrence and reminder field handling
- Microsoft-specific edge cases

Do not:

- modify it
- use it as the implementation base
- depend on it at runtime
- port its MCP server layer
- port its architecture wholesale
- translate files line-by-line into Python

### `reference/Microsoft-To-Do-Export`

Upstream:

```text
https://github.com/daylamtayari/Microsoft-To-Do-Export
```

This is a **Go** project.

Use it only as reference for:

- completeness of Microsoft To Do extraction
- completed-task handling
- notes/body handling
- checklist handling
- attachments and attachment metadata
- raw-export structure
- Graph pagination / API edge cases

Do not modify it or depend on it at runtime.

## General design

Build a new Python tool with a clean separation:

```text
device-code authentication
        ↓
read-only Microsoft Graph retrieval
        ↓
durable raw JSON snapshot
        ↓ offline boundary
normalized Python domain model
        ↓
RFC 5545 conversion
        ↓
ICS serializer
```

Keep authentication, Graph retrieval, snapshot storage, and conversion separate. Export must be
repeatable from a stored snapshot without Microsoft authentication or network access.

The conversion layer must be testable entirely from fixture JSON without Microsoft authentication or network access.

## CLI

Create a CLI named conceptually:

```bash
mstodo-to-ics
```

Expected commands:

```bash
mstodo-to-ics auth
mstodo-to-ics retrieve ./todo-snapshot
mstodo-to-ics export ./todo-snapshot ./todo-export
```

Useful flags:

```bash
mstodo-to-ics auth --config /path/to/settings.toml
mstodo-to-ics retrieve ./todo-snapshot --dry-run
mstodo-to-ics export ./todo-snapshot ./todo-export --dry-run
```

`auth` is the only command that may initiate device-code interaction. `retrieve` must use the
persisted cache and fail with an instruction to run `auth` when no usable login exists. `export`
must not authenticate or access the network.

## Output

Retrieval snapshot example:

```text
todo-snapshot/
├── retrieval.json
└── raw/
    ├── lists.json
    ├── tasks/
    ├── checklists/
    └── pages/
```

Export bundle example:

```text
todo-export/
├── manifest.json
├── Tasks.ics
├── Shopping.ics
├── House.ics
├── HabaQ.ics
└── raw/
    ├── lists.json
    └── tasks/
        ├── <list-id-1>.json
        ├── <list-id-2>.json
        └── ...
```

Sanitize filenames safely and prevent duplicate/similar list names from overwriting each other.

## Microsoft authentication

Use Microsoft Graph with the minimum practical **read-only** scopes.

Requirements:

- personal Microsoft accounts
- device-code OAuth preferred
- no manually pasted bearer tokens
- `auth` always stores a private plaintext cache in the current working directory
- invoking `auth` is the explicit opt-in to persistent token storage
- `retrieve` uses that cache non-interactively from the same working directory
- `export` has no authentication dependency
- no write permissions

Before implementing auth, inspect current official Microsoft Graph Python guidance and compare with the reference projects.

## Graph retrieval

Retrieve at least:

- task lists
- tasks
- completed tasks
- checklist items
- notes/body
- due/start dates
- importance
- categories
- recurrence
- reminder settings
- completion timestamps
- linked resources
- attachment metadata where available

Handle pagination correctly.

Preserve original API data in raw JSON.

Do not silently drop fields simply because they are not mapped into VTODO yet.

## VTODO mapping

Minimum mapping:

| Microsoft To Do | VTODO |
|---|---|
| task id | deterministic `UID` |
| title | `SUMMARY` |
| notes/body | `DESCRIPTION` |
| created datetime | `CREATED` |
| last modified datetime | `LAST-MODIFIED` |
| due datetime/date | `DUE` |
| start datetime/date | `DTSTART` |
| incomplete status | `STATUS:NEEDS-ACTION` |
| completed status | `STATUS:COMPLETED` |
| completed datetime | `COMPLETED` |
| importance | `PRIORITY` |
| categories | `CATEGORIES` |
| recurrence | detect, count, preserve in raw JSON, and warn |
| reminder | detect, count, preserve in raw JSON, and warn |
| linked resources | preserve in `DESCRIPTION` initially |

For recurrence and reminders, the warning is the complete v1 behavior, not a placeholder for
an `RRULE` or `VALARM` implementation in a later v1 milestone.

Also preserve source IDs:

```ics
X-MSTODO-LIST-ID:<list-id>
X-MSTODO-TASK-ID:<task-id>
```

UIDs must be stable between exports.

Do not create random UIDs on each run.

## Checklist items

Keep Microsoft checklist items as a checklist within the parent task. Do not convert them
into child `VTODO` components or model them as Nextcloud subtasks.

Append a deterministic, human-readable checklist block to the parent VTODO's `DESCRIPTION`,
after the original task notes. Represent completion without losing the original item text,
for example:

```text
Checklist:
- [ ] Buy paint
- [x] Measure wall
```

Preserve the structured checklist items, including their source IDs and completion state, in
raw JSON. The text representation must have deterministic ordering and must not invent
unavailable dates or metadata.

If Nextcloud requires an interoperability adjustment to display this checklist usefully,
document and test it explicitly, while retaining the no-child-`VTODO` design.

## Calendar structure

Produce **one `.ics` file per Microsoft To Do list**.

Each file should look conceptually like:

```ics
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//mstodo-to-ics//EN
CALSCALE:GREGORIAN

BEGIN:VTODO
...
END:VTODO

END:VCALENDAR
```

Do not merge all lists into a single file by default.

## Raw backup

The `retrieve` command always creates a durable, schema-versioned snapshot containing raw
Microsoft data and original Graph page envelopes:

```text
retrieval.json
raw/lists.json
raw/tasks/<list-id>.json
raw/checklists/<list-id>.json
raw/pages/...
```

The `export` command consumes this snapshot offline and also includes the source raw JSON in its
self-contained migration bundle. Re-running conversion after a code update requires only a new
destination:

```bash
mstodo-to-ics export ./todo-snapshot ./todo-export-v2
```

## Attachments

Attachments are not required for v1.

However:

1. detect them
2. report them in `manifest.json`
3. warn in CLI output
4. preserve metadata in raw JSON

Do not silently lose attachment information.

Do not block v1 on downloading attachments.

## Manifest

Generate `manifest.json` with totals and preferably per-list counts.

Example:

```json
{
  "lists": 7,
  "tasks": 438,
  "completed_tasks": 217,
  "checklist_items": 63,
  "recurring_tasks": 11,
  "tasks_with_reminders": 34,
  "attachments_detected": 2,
  "warnings": [
    "11 recurring task(s) detected; recurrence is preserved in raw JSON but not mapped to RRULE",
    "34 task(s) with reminders detected; reminders are preserved in raw JSON but not mapped to VALARM"
  ],
  "errors": []
}
```

## Validation

Before reporting success:

1. serialize each `.ics`
2. parse it again with a standards-compliant Python iCalendar library
3. verify:
   - VTODO count
   - required UIDs
   - no duplicate UIDs
   - expected checklist text is present in the parent description

Recurrence and reminder warnings must also be validated against the source-data counts in the
manifest. v1 does not emit `RRULE` or `VALARM`.

Prefer mature iCalendar libraries rather than hand-writing the full format.

## Date/time handling

Be conservative:

- preserve all-day vs timed semantics
- preserve UTC vs local/floating semantics where possible
- avoid accidental timezone shifts
- use RFC 5545-compliant output
- only emit timezone definitions when appropriate

Test:

- all-day due dates
- timed due dates
- DST boundaries
- completion timestamps
- recurrence detection and warning
- reminder detection and warning

## Escaping

Correctly handle:

- commas
- semicolons
- backslashes
- newlines
- Unicode
- long-line folding

Include tests with Danish characters:

```text
æ ø å Æ Ø Å
```

## Safety constraints

Microsoft access is strictly read-only.

Do not implement:

- task deletion
- task updates
- moving tasks
- completion changes
- list cleanup
- automatic Nextcloud upload
- CalDAV write access
- automatic Nextcloud list creation

This is an exporter only.

## Nextcloud target

Output is intended for manual import:

```text
Nextcloud Calendar
→ Settings
→ Import calendar
→ choose/create destination calendar
```

The Python tool must not depend on Nextcloud internals.

## First real migration test

Create a small Microsoft To Do list such as:

```text
Migration test
```

containing:

- ordinary task
- completed task
- task with notes
- task with due date
- task with start date
- task with category
- important task
- recurring task
- task with reminder
- task with two checklist items

Run the three commands, locate this list's generated `.ics` file, and manually import it into an
empty Nextcloud list/calendar.

Inspect in:

1. Nextcloud Tasks
2. Nextcloud Calendar
3. relevant CalDAV client if useful

Confirm that checklist text remains readable on the parent task and that recurring tasks and
reminders are called out by the CLI and manifest rather than silently omitted.

## Tests

Add tests for at least:

- active task
- completed task
- multiline notes
- Danish/Unicode text
- due date
- start date
- priority
- multiple categories
- recurrence detection, count, raw preservation, and warning
- reminder detection, count, raw preservation, and warning
- checklist collection and parent-description rendering
- deterministic UID
- filename sanitization
- duplicate list names
- empty list
- long description
- ICS escaping
- serialize-then-parse validation
- Graph pagination
- raw JSON preservation

Normal unit tests should use fixture JSON and require no network access.

## Suggested Python package layout

```text
src/mstodo_to_ics/
├── __init__.py
├── auth.py
├── graph.py
├── models.py
├── convert.py
├── ics.py
├── snapshot.py
├── export.py
└── cli.py
```

Suggested responsibilities:

- `auth.py`: device-code auth / token cache
- `graph.py`: Graph retrieval and pagination
- `models.py`: normalized Python domain objects
- `convert.py`: Graph/raw → domain model
- `ics.py`: domain model → RFC 5545
- `snapshot.py`: transactional raw snapshot storage and loading
- `export.py`: ICS files, preserved raw JSON, and export manifest
- `cli.py`: command line

This layout is guidance, not a rigid requirement.

## Python quality expectations

Use:

- type hints
- `pathlib`
- lightweight dataclasses/models
- clear exceptions
- pytest
- deterministic tests
- simple readable Python

Ruff is a reasonable choice for formatting/linting.

Do not let tooling complexity dominate the project.

## Implementation order

1. Inspect both repos under `reference/`.
2. Identify in each:
   - authentication
   - Graph endpoints
   - pagination
   - list retrieval
   - task retrieval
   - completed-task handling
   - checklist handling
   - recurrence
   - reminders
   - attachments
   - VTODO/ICS behavior
3. Check current Microsoft Graph Python documentation.
4. Propose minimal Python architecture and dependencies.
5. Initialize Python project.
6. Implement normalized models and fixture-driven conversion.
7. Implement ICS generation and validation.
8. Add raw JSON and manifest output.
9. Implement Graph retrieval.
10. Implement device-code authentication.
11. Add the staged `auth` → `retrieve` → offline `export` CLI and durable snapshot format.
12. Add checklist retrieval, raw preservation, and parent-description rendering.
13. Complete remaining timezone/escaping tests.
14. Run tests/lint/typecheck.
15. Perform small real `Migration test` export.
16. Manually test import into Nextcloud.
17. Adjust only where actual interoperability requires it.

## Definition of done for v1

v1 is complete when:

- product is Python
- personal Microsoft device-code login works
- `auth` explicitly creates the private persistent cache
- `retrieve` is non-interactive and creates a durable raw snapshot
- `export` can be rerun offline from that snapshot after code changes
- Graph access is read-only
- all To Do lists can be enumerated
- one `.ics` file is generated per list
- active/completed states migrate
- notes migrate
- start/due dates migrate
- importance migrates
- categories migrate
- recurrence is detected, counted, preserved in raw JSON, and reported with a warning
- reminders are detected, counted, preserved in raw JSON, and reported with a warning
- checklist items are preserved in raw JSON and rendered as a checklist in the parent task description
- raw JSON is preserved
- attachments are detected/reported
- generated ICS self-validates
- no Nextcloud credentials are required
- unit tests run offline
- reference repos remain unchanged

## Non-goals for v1

Do not build:

- TypeScript implementation
- Rust implementation
- bidirectional sync
- incremental sync
- automatic repeated migration
- Microsoft cleanup
- Nextcloud API integration
- attachment upload into Nextcloud
- recurrence-to-`RRULE` conversion
- reminder-to-`VALARM` conversion
- checklist-to-child-`VTODO` conversion
- GUI
- web app
- daemon/service
- MCP server

## Codex working style

Start by inspecting the reference repositories before substantial implementation.

Treat both as read-only prior art.

Do not assume either reference repo should determine the Python architecture.

Before implementation, report:

1. relevant modules/files in each reference repo
2. useful behavior to preserve
3. current Microsoft Graph Python auth/API options
4. proposed Python dependency set
5. proposed package layout
6. important differences between the two references
7. smallest first implementation milestone

Then proceed.

After each stage:

- run tests
- run lint/typecheck if configured
- fix failures iteratively
- keep the project runnable
- prefer small coherent changes

Do not stop for routine design decisions once the architecture has been agreed.

## Initial Codex task

Begin with repository reconnaissance.

Specifically:

1. inspect:
   - `reference/mcp-microsoft-todo`
   - `reference/Microsoft-To-Do-Export`
2. locate in each:
   - OAuth/device-code implementation
   - Graph client/API calls
   - list retrieval
   - task retrieval
   - completed-task handling
   - checklist retrieval
   - pagination
   - recurrence handling
   - reminder handling
   - attachment handling
   - ICS/VTODO generation if present
3. compare behavior and note important edge cases
4. check current official Microsoft Graph guidance for Python device-code authentication and To Do access
5. propose a minimal Python package architecture and dependency set
6. explain which behavior will be reimplemented versus merely used as reference
7. do **not** modify either `reference/` repository

After presenting the reconnaissance and architecture, proceed to initialize the Python project and implement the first milestone unless there is a genuine blocker.
