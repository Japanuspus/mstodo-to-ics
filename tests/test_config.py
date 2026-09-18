from __future__ import annotations

from pathlib import Path

import pytest

from mstodo_to_ics.config import (
    DEFAULT_CONFIG_FILENAME,
    ConfigError,
    load_config,
)


def test_missing_default_config_is_optional(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    config = load_config()

    assert config.path == tmp_path / DEFAULT_CONFIG_FILENAME
    assert config.client_id is None


def test_loads_default_config_from_working_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / DEFAULT_CONFIG_FILENAME
    config_path.write_text('[auth]\nclient_id = "  configured-client-id  "\n', encoding="utf-8")

    config = load_config()

    assert config.path == config_path
    assert config.client_id == "configured-client-id"


def test_loads_manually_selected_relative_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / "settings" / "export.toml"
    config_path.parent.mkdir()
    config_path.write_text('[auth]\nclient_id = "manual-client-id"\n', encoding="utf-8")

    config = load_config(Path("settings/export.toml"))

    assert config.path == config_path
    assert config.client_id == "manual-client-id"


def test_missing_manually_selected_config_is_an_error(tmp_path: Path) -> None:
    missing = tmp_path / "missing.toml"

    with pytest.raises(ConfigError, match="configuration file does not exist"):
        load_config(missing)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("not = valid = toml", "could not read configuration file"),
        ('client_id = "wrong-level"\n', "unknown top-level configuration key"),
        ('auth = "not-a-table"\n', r"section \[auth\] must be a table"),
        ("[auth]\nunknown = true\n", r"unknown \[auth\] configuration key"),
        ('[auth]\nclient_id = "   "\n', r"\[auth\]\.client_id must be a non-empty string"),
        ("[auth]\nclient_id = 123\n", r"\[auth\]\.client_id must be a non-empty string"),
    ],
)
def test_rejects_invalid_configuration(
    tmp_path: Path,
    content: str,
    message: str,
) -> None:
    config_path = tmp_path / "invalid.toml"
    config_path.write_text(content, encoding="utf-8")

    with pytest.raises(ConfigError, match=message):
        load_config(config_path)
