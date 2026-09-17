from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from mstodo_to_ics.models import TodoList
from mstodo_to_ics.normalize import normalize_list

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "graph"


def _load_json(name: str) -> Any:
    with (FIXTURE_DIR / name).open(encoding="utf-8") as fixture_file:
        return json.load(fixture_file)


@pytest.fixture
def normalized_list() -> TodoList:
    raw_list = _load_json("list.json")
    raw_tasks = _load_json("tasks.json")["value"]
    return normalize_list(raw_list, raw_tasks)
