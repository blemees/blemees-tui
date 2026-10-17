"""TOML config loader (spec §12).

Override precedence: CLI flags > env vars (``BLEMEES_TUI_*``) > config file
> built-in defaults. CLI overrides are applied by the app after the config
file and env vars are loaded; this module knows about defaults, file
parsing, and env-var overlays.

Config file path: ``load_config(path)`` arg (``--config``) >
``$BLEMEES_TUI_CONFIG`` > :func:`default_config_path`.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    """The config file the user chose (``--config`` / ``$BLEMEES_TUI_CONFIG``) is unusable."""


# ---------------------------------------------------------------------------
# Dataclass shape
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConnectionConfig:
    socket: str = ""  # empty = auto-resolve via daemon precedence


@dataclass(frozen=True)
class UiConfig:
    theme: str = "dark"  # dark | light
    show_thinking: bool = False


@dataclass(frozen=True)
class LoggingConfig:
    level: str = "info"  # debug | info | warn | error
    keep_days: int = 7


@dataclass(frozen=True)
class Config:
    connection: ConnectionConfig = field(default_factory=ConnectionConfig)
    ui: UiConfig = field(default_factory=UiConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# File / env resolution
# ---------------------------------------------------------------------------


def default_config_path() -> Path:
    """``$XDG_CONFIG_HOME/blemees/tui.toml`` (or ``~/.config/blemees/tui.toml``)."""
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "blemees" / "tui.toml"


def _coerce(target_type: type, raw: Any) -> Any:
    if target_type is bool:
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            return raw.strip().lower() in {"1", "true", "yes", "on"}
        return bool(raw)
    if target_type is int:
        return int(raw)
    if target_type is str:
        return str(raw)
    return raw


_SECTIONS: dict[str, type] = {
    "connection": ConnectionConfig,
    "ui": UiConfig,
    "logging": LoggingConfig,
}


def _field_type(section_cls: type, field_name: str) -> type:
    """Resolve a dataclass field's runtime type. Survives PEP 563 string annotations."""
    base = section_cls()
    return type(getattr(base, field_name))


def _build_section(section_cls: type, raw: dict[str, Any] | None):
    raw = raw or {}
    base = section_cls()
    overrides: dict[str, Any] = {}
    for f in section_cls.__dataclass_fields__.values():  # type: ignore[attr-defined]
        if f.name in raw:
            overrides[f.name] = _coerce(_field_type(section_cls, f.name), raw[f.name])
    return replace(base, **overrides) if overrides else base


def load_config(path: Path | None = None) -> Config:
    """Load + validate config from disk (or built-in defaults if absent).

    Unknown sections and keys are ignored, so config files written for
    older versions keep loading. A missing file at the default path means
    built-in defaults; a missing file the user chose (``path`` /
    ``$BLEMEES_TUI_CONFIG``) raises :class:`ConfigError`.
    """
    env_path = os.environ.get("BLEMEES_TUI_CONFIG")
    if path is not None:
        cfg_path, source = path, "--config"
    elif env_path:
        cfg_path, source = Path(env_path).expanduser(), "$BLEMEES_TUI_CONFIG"
    else:
        cfg_path, source = default_config_path(), None
    if source is not None and not cfg_path.exists():
        raise ConfigError(f"config file not found: {cfg_path} (from {source})")
    raw: dict[str, Any] = {}
    if cfg_path.exists():
        with cfg_path.open("rb") as fh:
            raw = tomllib.load(fh)

    sections: dict[str, Any] = {
        name: _build_section(cls, raw.get(name)) for name, cls in _SECTIONS.items()
    }
    cfg = Config(**sections)
    return apply_env_overrides(cfg)


# ---------------------------------------------------------------------------
# Env-var overlay
# ---------------------------------------------------------------------------


# Mapping: env var → (section, field). Keep this list short and explicit.
_ENV_OVERRIDES: dict[str, tuple[str, str]] = {
    "BLEMEES_TUI_SOCKET": ("connection", "socket"),
    "BLEMEES_TUI_THEME": ("ui", "theme"),
    "BLEMEES_TUI_LOG_LEVEL": ("logging", "level"),
}


def apply_env_overrides(cfg: Config) -> Config:
    new_sections: dict[str, Any] = {}
    for env_name, (section, field_name) in _ENV_OVERRIDES.items():
        if env_name not in os.environ:
            continue
        section_obj = new_sections.get(section, getattr(cfg, section))
        section_cls = type(section_obj)
        coerced = _coerce(_field_type(section_cls, field_name), os.environ[env_name])
        new_sections[section] = replace(section_obj, **{field_name: coerced})
    if not new_sections:
        return cfg
    return replace(cfg, **new_sections)


def apply_cli_overrides(
    cfg: Config,
    *,
    socket: str | None = None,
    log_level: str | None = None,
) -> Config:
    """Apply argparse-style overrides on top of file + env."""
    changes: dict[str, Any] = {}
    if socket is not None:
        changes["connection"] = replace(cfg.connection, socket=socket)
    if log_level is not None:
        changes["logging"] = replace(cfg.logging, level=log_level)
    return replace(cfg, **changes) if changes else cfg
