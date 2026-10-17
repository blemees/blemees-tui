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
show_thinking = true

[logging]
level = "debug"
keep_days = 14
""",
        encoding="utf-8",
    )
    cfg = load_config(p)
    assert cfg.connection.socket == "/tmp/explicit.sock"
    assert cfg.ui.theme == "light"
    assert cfg.ui.show_thinking is True
    assert cfg.logging.level == "debug"
    assert cfg.logging.keep_days == 14


def test_old_config_with_removed_keys_still_loads(tmp_path: Path):
    # [defaults], [keybindings], ui.sidebar_width and ui.markdown_code_theme
    # were dropped (never consumed); old files must load, keys ignored.
    p = tmp_path / "config.toml"
    p.write_text(
        """
[defaults]
backend = "codex"
claude_model = "opus"
codex_model = "gpt-5.2-codex"
claude_permission_mode = "plan"
codex_sandbox = "read-only"
cwd = "/proj"

[ui]
theme = "light"
sidebar_width = 40
markdown_code_theme = "monokai"

[keybindings]
new_session = "ctrl+m"
""",
        encoding="utf-8",
    )
    cfg = load_config(p)
    assert cfg.ui.theme == "light"
    assert not hasattr(cfg, "defaults")
    assert not hasattr(cfg, "keybindings")


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


def test_env_var_config_path_is_loaded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    p = tmp_path / "from-env.toml"
    p.write_text('[ui]\ntheme = "light"\n', encoding="utf-8")
    monkeypatch.setenv("BLEMEES_TUI_CONFIG", str(p))
    cfg = load_config()
    assert cfg.ui.theme == "light"


def test_explicit_path_beats_env_var_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    env_file = tmp_path / "from-env.toml"
    env_file.write_text('[logging]\nlevel = "error"\n', encoding="utf-8")
    explicit = tmp_path / "explicit.toml"
    explicit.write_text('[logging]\nlevel = "debug"\n', encoding="utf-8")
    monkeypatch.setenv("BLEMEES_TUI_CONFIG", str(env_file))
    cfg = load_config(explicit)
    assert cfg.logging.level == "debug"


def test_app_honours_config_path_override(isolated_state_dir: Path):
    from blemees_tui.app import BlemeesTuiApp

    p = isolated_state_dir / "override.toml"
    p.write_text('[connection]\nsocket = "/tmp/from-override.sock"\n', encoding="utf-8")
    app = BlemeesTuiApp(config_path_override=str(p))
    assert app.config_obj.connection.socket == "/tmp/from-override.sock"


@pytest.mark.asyncio
async def test_app_applies_ui_theme(isolated_state_dir: Path, monkeypatch: pytest.MonkeyPatch):
    from blemees_tui.app import BlemeesTuiApp

    async def _noop(self):
        return

    monkeypatch.setattr("blemees_tui.connection.Connection.start", _noop)
    monkeypatch.setattr("blemees_tui.connection.Connection.stop", _noop)
    monkeypatch.setenv("BLEMEES_TUI_THEME", "light")
    app = BlemeesTuiApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.theme == "textual-light"
        assert not app.current_theme.dark
