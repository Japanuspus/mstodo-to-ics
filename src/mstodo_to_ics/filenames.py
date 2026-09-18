"""Safe and deterministic output filename allocation."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable

from .models import TodoList

_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WHITESPACE = re.compile(r"\s+")
_RESERVED_WINDOWS_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}
_MAX_STEM_LENGTH = 100


def _id_suffix(source_id: str) -> str:
    return hashlib.sha256(source_id.encode("utf-8")).hexdigest()[:16]


def stable_source_key(source_id: str) -> str:
    """Return a portable, deterministic key for raw files containing a source ID."""
    return hashlib.sha256(source_id.encode("utf-8")).hexdigest()


def sanitize_filename_stem(display_name: str) -> str:
    """Return a readable filename stem portable across common filesystems."""
    stem = unicodedata.normalize("NFC", display_name)
    stem = _FORBIDDEN.sub("_", stem)
    stem = _WHITESPACE.sub(" ", stem).strip(" .")
    stem = stem[:_MAX_STEM_LENGTH].rstrip(" .")
    if not any(character.isalnum() for character in stem):
        return ""
    if stem.upper() in _RESERVED_WINDOWS_NAMES:
        stem = f"_{stem}"
    return stem


def allocate_list_filenames(lists: Iterable[TodoList]) -> dict[str, str]:
    """Allocate deterministic, collision-safe `.ics` names keyed by list ID."""
    materialized = tuple(lists)
    ids = [task_list.source_id for task_list in materialized]
    if len(ids) != len(set(ids)):
        raise ValueError("cannot allocate filenames for duplicate list IDs")

    stems = {item.source_id: sanitize_filename_stem(item.display_name) for item in materialized}
    counts = Counter(stem.casefold() for stem in stems.values() if stem)
    result: dict[str, str] = {}
    for item in materialized:
        stem = stems[item.source_id]
        if not stem:
            stem = "List"
            needs_suffix = True
        else:
            needs_suffix = counts[stem.casefold()] > 1
        if needs_suffix:
            stem = f"{stem}--{_id_suffix(item.source_id)}"
        result[item.source_id] = f"{stem}.ics"

    allocated = tuple(name.casefold() for name in result.values())
    if len(allocated) != len(set(allocated)):
        raise ValueError("list IDs produced colliding output filenames")
    return result
