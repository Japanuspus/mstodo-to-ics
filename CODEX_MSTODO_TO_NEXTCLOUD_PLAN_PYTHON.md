# Codex Brief: Microsoft To Do → Nextcloud Tasks migration exporter (Python)

## Goal

Build a small standalone **Python CLI tool** that exports Microsoft To Do data to **one RFC 5545 `.ics` file per Microsoft To Do list**, suitable for **manual import into Nextcloud Calendar / Tasks**.

The implementation must be a new Python project. Existing repositories under `reference/` are prior art only and must not become the project architecture.

Primary flow:

```text
Microsoft To Do
    │
    │ Microsoft Graph, read-only
    ▼
mstodo-to-ics (Python)
    │
    ├── one .ics file per list
    ├── raw source JSON
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
- recurrence → RRULE mapping
- reminder → VALARM mapping
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
- checklist/subtask handling
- attachments and attachment metadata
- raw-export structure
- Graph pagination / API edge cases

Do not modify it or depend on it at runtime.

## General design

Build a new Python tool with a clean separation:

```text
Microsoft Graph JSON
        ↓
normalized Python domain model
        ↓
RFC 5545 conversion
        ↓
ICS serializer
```

Keep Graph retrieval separate from conversion.

The conversion layer must be testable entirely from fixture JSON without Microsoft authentication or network access.

## CLI

Create a CLI named conceptually:

```bash
mstodo-to-ics
```

Expected commands:

```bash
mstodo-to-ics auth
mstodo-to-ics lists
mstodo-to-ics export ./todo-export
```

Useful optional commands/flags:

```bash
mstodo-to-ics export ./todo-export --list "House"
mstodo-to-ics export ./todo-export --list-id <id>
mstodo-to-ics export ./todo-export --dry-run
```

## Output

Example:

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
- local token cache where practical
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
| recurrence | `RRULE` |
| reminder | `VALARM` |
| linked resources | preserve in `DESCRIPTION` initially |

Also preserve source IDs:

```ics
X-MSTODO-LIST-ID:<list-id>
X-MSTODO-TASK-ID:<task-id>
```

UIDs must be stable between exports.

Do not create random UIDs on each run.

## Checklist items / subtasks

Convert checklist items into child `VTODO` components when possible.

Use:

```ics
RELATED-TO;RELTYPE=PARENT:<parent-uid>
```

Each checklist item gets its own stable UID and:

```ics
X-MSTODO-CHECKLIST-ID:<checklist-id>
```

Map checklist completion state to VTODO status.

Do not invent unavailable dates/metadata.

If Nextcloud requires an interoperability adjustment, document and test it explicitly.

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

Always preserve raw Microsoft data:

```text
raw/lists.json
raw/tasks/<list-id>.json
```

The raw export is part of the migration safety strategy.

Design conversion so a future offline command such as:

```bash
mstodo-to-ics convert-raw ./todo-export/raw ./converted
```

would be straightforward to add.

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
  "warnings": [],
  "errors": []
}
```

## Validation

Before reporting success:

1. serialize each `.ics`
2. parse it again with a standards-compliant Python iCalendar library
3. verify:
   - parent VTODO count
   - child VTODO count
   - required UIDs
   - no duplicate UIDs
   - valid parent references
   - parseable RRULEs
   - parseable VALARMs

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
- reminders
- recurring tasks

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

Export only this list and manually import it into an empty Nextcloud list/calendar.

Inspect in:

1. Nextcloud Tasks
2. Nextcloud Calendar
3. relevant CalDAV client if useful

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
- recurrence
- VALARM
- checklist/subtask relation
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
├── export.py
└── cli.py
```

Suggested responsibilities:

- `auth.py`: device-code auth / token cache
- `graph.py`: Graph retrieval and pagination
- `models.py`: normalized Python domain objects
- `convert.py`: Graph/raw → domain model
- `ics.py`: domain model → RFC 5545
- `export.py`: files/raw JSON/manifest
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
11. Add CLI.
12. Add checklist → child VTODO conversion.
13. Add recurrence/reminder/timezone/escaping tests.
14. Run tests/lint/typecheck.
15. Perform small real `Migration test` export.
16. Manually test import into Nextcloud.
17. Adjust only where actual interoperability requires it.

## Definition of done for v1

v1 is complete when:

- product is Python
- personal Microsoft device-code login works
- Graph access is read-only
- all To Do lists can be enumerated
- one `.ics` file is generated per list
- active/completed states migrate
- notes migrate
- start/due dates migrate
- importance migrates
- categories migrate
- recurrence migrates
- reminders migrate
- checklist items become usable subtasks, or incompatibility is documented with a deterministic fallback
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
   - recurrence mapping
   - reminder mapping
   - attachment handling
   - ICS/VTODO generation if present
3. compare behavior and note important edge cases
4. check current official Microsoft Graph guidance for Python device-code authentication and To Do access
5. propose a minimal Python package architecture and dependency set
6. explain which behavior will be reimplemented versus merely used as reference
7. do **not** modify either `reference/` repository

After presenting the reconnaissance and architecture, proceed to initialize the Python project and implement the first milestone unless there is a genuine blocker.
