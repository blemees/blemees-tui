"""TOML config loader (spec §12).

Override precedence: env vars (``BLEMEES_TUI_*``) > CLI flags > config file
> built-in defaults. CLI overrides are applied by ``__main__`` after the
config file is loaded; this module knows about defaults, file parsing, and
env-var overlays.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Dataclass shape
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConnectionConfig:
    socket: str = ""  # empty = auto-resolve via daemon precedence


@dataclass(frozen=True)
class DefaultsConfig:
    backend: str = ""  # empty = first advertised by hello_ack
    claude_model: str = "sonnet"
    codex_model: str = "gpt-5.2-codex"
    claude_permission_mode: str = "default"
    codex_sandbox: str = "workspace-write"
    cwd: str = ""  # empty = $PWD at launch


@dataclass(frozen=True)
class UiConfig:
    theme: str = "dark"  # dark | light
    sidebar_width: int = 28
    show_thinking: bool = False
    markdown_code_theme: str = "monokai"


@dataclass(frozen=True)
class LoggingConfig:
    level: str = "info"  # debug | info | warn | error
    keep_days: int = 7


@dataclass(frozen=True)
class Config:
    connection: ConnectionConfig = field(default_factory=ConnectionConfig)
    defaults: DefaultsConfig = field(default_factory=DefaultsConfig)
    ui: UiConfig = field(default_factory=UiConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    keybindings: dict[str, str] = field(default_factory=dict)

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
    "defaults": DefaultsConfig,
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
    """Load + validate config from disk (or built-in defaults if absent)."""
    cfg_path = path or default_config_path()
    raw: dict[str, Any] = {}
    if cfg_path.exists():
        with cfg_path.open("rb") as fh:
            raw = tomllib.load(fh)

    sections: dict[str, Any] = {
        name: _build_section(cls, raw.get(name)) for name, cls in _SECTIONS.items()
    }
    keybindings_raw = raw.get("keybindings") or {}
    if not isinstance(keybindings_raw, dict):
        raise ValueError("[keybindings] must be a table of action → key strings")
    keybindings = {str(k): str(v) for k, v in keybindings_raw.items()}

    cfg = Config(**sections, keybindings=keybindings)
    return apply_env_overrides(cfg)


# ---------------------------------------------------------------------------
# Env-var overlay
# ---------------------------------------------------------------------------


# Mapping: env var → (section, field). Keep this list short and explicit.
_ENV_OVERRIDES: dict[str, tuple[str, str]] = {
    "BLEMEES_TUI_SOCKET": ("connection", "socket"),
    "BLEMEES_TUI_THEME": ("ui", "theme"),
    "BLEMEES_TUI_LOG_LEVEL": ("logging", "level"),
    "BLEMEES_TUI_BACKEND": ("defaults", "backend"),
    "BLEMEES_TUI_CWD": ("defaults", "cwd"),
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
