"""Normalized domain values independent of Graph transport and iCalendar."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _parse_iso_datetime(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
    try:
        return datetime.fromisoformat(normalized)
    except ValueError as error:
        raise ValueError(f"invalid ISO 8601 date-time: {value!r}") from error


@dataclass(frozen=True, slots=True)
class GraphInstant:
    """An absolute Graph DateTimeOffset, retaining its exact source spelling."""

    source: str

    def __post_init__(self) -> None:
        parsed = _parse_iso_datetime(self.source)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError(f"Graph instant must include an offset: {self.source!r}")

    @property
    def value(self) -> datetime:
        """Return the represented instant as an offset-aware datetime."""
        return _parse_iso_datetime(self.source)

    @property
    def utc(self) -> datetime:
        """Return the represented instant in UTC, never in the machine timezone."""
        return self.value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class GraphDateTime:
    """A Graph dateTimeTimeZone value without collapsing its timezone semantics.

    Graph represents these values as a timezone-free wall-clock string plus a
    separate timezone identifier. Both source values remain available verbatim.
    """

    date_time: str
    time_zone: str

    def __post_init__(self) -> None:
        if not self.time_zone.strip():
            raise ValueError("Graph dateTimeTimeZone must include a timezone")
        parsed = _parse_iso_datetime(self.date_time)
        if parsed.tzinfo is not None and parsed.utcoffset() is not None:
            raise ValueError(
                "Graph dateTimeTimeZone.dateTime must be a local wall-clock value "
                f"without an offset: {self.date_time!r}"
            )

    @property
    def wall_time(self) -> datetime:
        """Return the offset-naive wall time associated with ``time_zone``."""
        return _parse_iso_datetime(self.date_time)

    def as_aware(self) -> datetime:
        """Resolve the wall time through its declared zone, never the machine zone.

        Graph can return Windows timezone names, which ``zoneinfo`` cannot resolve.
        Until a deliberate Windows-to-IANA mapping is added, those identifiers fail
        explicitly when an absolute instant is required instead of being guessed.
        """
        if self.time_zone.casefold() in {"utc", "etc/utc", "gmt", "z"}:
            return self.wall_time.replace(tzinfo=UTC)
        try:
            zone = ZoneInfo(self.time_zone)
        except ZoneInfoNotFoundError as error:
            raise ValueError(f"unsupported Graph timezone: {self.time_zone!r}") from error
        return self.wall_time.replace(tzinfo=zone)


@dataclass(frozen=True, slots=True)
class TaskBody:
    """Task body content and its Graph content type."""

    content: str
    content_type: str


@dataclass(frozen=True, slots=True)
class ChecklistItem:
    """One Microsoft To Do checklist item retained on its parent task."""

    source_id: str
    display_name: str
    is_checked: bool


@dataclass(frozen=True, slots=True)
class TodoTask:
    """Normalized fields currently mapped to a parent VTODO."""

    source_id: str
    list_id: str
    title: str
    body: TaskBody
    status: str
    importance: str
    created_at: GraphInstant
    modified_at: GraphInstant
    completed_at: GraphDateTime | None
    due_at: GraphDateTime | None
    start_at: GraphDateTime | None
    categories: tuple[str, ...]
    checklist_items: tuple[ChecklistItem, ...] = ()


@dataclass(frozen=True, slots=True)
class TodoList:
    """A Microsoft To Do list and its normalized parent tasks."""

    source_id: str
    display_name: str
    tasks: tuple[TodoTask, ...] = ()
