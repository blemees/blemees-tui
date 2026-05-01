"""Shared pytest fixtures."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture
def isolated_state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    return tmp_path


@pytest.fixture(autouse=True)
def _clean_blemees_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop any caller env that could leak between tests."""
    for key in list(os.environ):
        if key.startswith("BLEMEES_TUI_") or key == "BLEMEESD_SOCKET":
            monkeypatch.delenv(key, raising=False)
