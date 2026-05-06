"""Config loader tests (spec §12)."""

from __future__ import annotations

from pathlib import Path

import pytest

from blemees_tui.config import (
    Config,
    apply_cli_overrides,
    apply_env_overrides,
    load_config,
)


def test_defaults_when_no_file(tmp_path: Path):
    cfg = load_config(tmp_path / "absent.toml")
    assert isinstance(cfg, Config)
    assert cfg.connection.socket == ""
    assert cfg.ui.theme == "dark"
    assert cfg.logging.level == "info"


def test_toml_file_overrides(tmp_path: Path):
    p = tmp_path / "config.toml"
    p.write_text(
        """
[connection]
socket = "/tmp/explicit.sock"

[ui]
theme = "light"
sidebar_width = 40
show_thinking = true

[logging]
level = "debug"
keep_days = 14

[keybindings]
new_session = "ctrl+m"
""",
        encoding="utf-8",
    )
    cfg = load_config(p)
    assert cfg.connection.socket == "/tmp/explicit.sock"
    assert cfg.ui.theme == "light"
    assert cfg.ui.sidebar_width == 40
    assert cfg.ui.show_thinking is True
    assert cfg.logging.level == "debug"
    assert cfg.logging.keep_days == 14
    assert cfg.keybindings == {"new_session": "ctrl+m"}


def test_env_overrides_layered_on_top(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    p = tmp_path / "config.toml"
    p.write_text(
        """
[ui]
theme = "dark"
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("BLEMEES_TUI_THEME", "light")
    cfg = load_config(p)
    assert cfg.ui.theme == "light"


def test_cli_overrides_top_priority():
    cfg = Config()
    out = apply_cli_overrides(cfg, socket="/cli.sock", log_level="debug")
    assert out.connection.socket == "/cli.sock"
    assert out.logging.level == "debug"


def test_env_apply_idempotent():
    cfg = Config()
    assert apply_env_overrides(cfg) is cfg  # no env set → same instance
