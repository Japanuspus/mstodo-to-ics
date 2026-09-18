"""Load the small, local TOML configuration used by the CLI."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_FILENAME = ".mstodo-to-ics.toml"


class ConfigError(ValueError):
    """Raised when an explicitly selected or discovered config is invalid."""


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Validated application configuration and its resolved source path."""

    path: Path
    client_id: str | None = None


def _unknown_keys(value: dict[str, Any], allowed: set[str]) -> tuple[str, ...]:
    return tuple(sorted(set(value) - allowed))


def load_config(path: Path | None = None) -> AppConfig:
    """Load a config path, or optionally discover the default file in the CWD."""
    explicitly_selected = path is not None
    config_path = (path if path is not None else Path.cwd() / DEFAULT_CONFIG_FILENAME).absolute()

    if not config_path.exists():
        if explicitly_selected:
            raise ConfigError(f"configuration file does not exist: {config_path}")
        return AppConfig(path=config_path)
    if not config_path.is_file():
        raise ConfigError(f"configuration path is not a file: {config_path}")

    try:
        with config_path.open("rb") as handle:
            document = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ConfigError(f"could not read configuration file {config_path}: {error}") from error

    unknown_root = _unknown_keys(document, {"auth"})
    if unknown_root:
        names = ", ".join(unknown_root)
        raise ConfigError(f"unknown top-level configuration key(s): {names}")

    raw_auth = document.get("auth")
    if raw_auth is None:
        return AppConfig(path=config_path)
    if not isinstance(raw_auth, dict):
        raise ConfigError("configuration section [auth] must be a table")

    unknown_auth = _unknown_keys(raw_auth, {"client_id"})
    if unknown_auth:
        names = ", ".join(unknown_auth)
        raise ConfigError(f"unknown [auth] configuration key(s): {names}")

    raw_client_id = raw_auth.get("client_id")
    if raw_client_id is None:
        return AppConfig(path=config_path)
    if not isinstance(raw_client_id, str) or not raw_client_id.strip():
        raise ConfigError("[auth].client_id must be a non-empty string")

    return AppConfig(path=config_path, client_id=raw_client_id.strip())
