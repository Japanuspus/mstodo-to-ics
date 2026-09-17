from __future__ import annotations

from mstodo_to_ics.filenames import allocate_list_filenames, sanitize_filename_stem
from mstodo_to_ics.models import TodoList


def test_sanitizes_portably_without_destroying_unicode() -> None:
    assert sanitize_filename_stem('  Hus: æ ø å / "Plan".  ') == "Hus_ æ ø å _ _Plan_"
    assert sanitize_filename_stem("CON") == "_CON"


def test_duplicate_names_receive_deterministic_distinct_suffixes() -> None:
    lists = (
        TodoList(source_id="list-a", display_name="House"),
        TodoList(source_id="list-b", display_name="house"),
    )

    first = allocate_list_filenames(lists)
    second = allocate_list_filenames(reversed(lists))

    assert first == second
    assert first["list-a"] != first["list-b"]
    assert all(name.endswith(".ics") for name in first.values())


def test_empty_sanitized_name_is_safe_and_stable() -> None:
    task_list = TodoList(source_id="empty-list", display_name='<>:"/\\|?*')

    filename = allocate_list_filenames([task_list])["empty-list"]

    assert filename.startswith("List--")
    assert filename.endswith(".ics")
