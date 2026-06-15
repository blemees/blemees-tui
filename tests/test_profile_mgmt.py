"""Profile management flow in the app (#5): save (create/update), delete,
and graceful agent_unavailable."""

from __future__ import annotations

import pytest

textual = pytest.importorskip("textual")

from blemees_tui.app import BlemeesTuiApp  # noqa: E402
from blemees_tui.connection import ConnectionError_  # noqa: E402
from blemees_tui.widgets.modals import AgentEditorModal  # noqa: E402


async def _start_app_no_socket(monkeypatch):
    async def _noop(self):
        return

    monkeypatch.setattr("blemees_tui.connection.Connection.start", _noop)
    monkeypatch.setattr("blemees_tui.connection.Connection.stop", _noop)


@pytest.mark.asyncio
async def test_save_profile_creates(isolated_state_dir, monkeypatch):
    await _start_app_no_socket(monkeypatch)
    created: list[tuple] = []

    async def fake_create(self, name, spec):
        created.append((name, spec))
        return {"type": "profile.created", "name": name}

    monkeypatch.setattr("blemees_tui.connection.Connection.create_profile", fake_create)
    app = BlemeesTuiApp()
    async with app.run_test():
        spec = {"agent": {"agent_command": "claude-agent-acp"}}
        await app.on_agent_editor_modal_save_profile(AgentEditorModal.SaveProfile("mine", spec))
        assert created == [("mine", spec)]


@pytest.mark.asyncio
async def test_save_profile_falls_back_to_update_on_exists(isolated_state_dir, monkeypatch):
    await _start_app_no_socket(monkeypatch)
    updated: list[str] = []

    async def fake_create(self, name, spec):
        raise ConnectionError_("profile_exists: already exists")

    async def fake_update(self, name, spec):
        updated.append(name)
        return {"type": "profile.updated", "name": name}

    monkeypatch.setattr("blemees_tui.connection.Connection.create_profile", fake_create)
    monkeypatch.setattr("blemees_tui.connection.Connection.update_profile", fake_update)
    app = BlemeesTuiApp()
    async with app.run_test():
        await app.on_agent_editor_modal_save_profile(
            AgentEditorModal.SaveProfile("mine", {"agent": {}})
        )
        assert updated == ["mine"]


@pytest.mark.asyncio
async def test_save_profile_agent_unavailable_is_graceful(isolated_state_dir, monkeypatch):
    await _start_app_no_socket(monkeypatch)

    async def fake_create(self, name, spec):
        raise ConnectionError_("agent_unavailable: 'nope' not on PATH")

    monkeypatch.setattr("blemees_tui.connection.Connection.create_profile", fake_create)
    app = BlemeesTuiApp()
    async with app.run_test():
        # Must not raise — the error is surfaced, not fatal.
        await app.on_agent_editor_modal_save_profile(
            AgentEditorModal.SaveProfile("bad", {"agent": {}})
        )
        log = [e.category for e in app.state.event_log]
        assert "profile_save_failed" in log


@pytest.mark.asyncio
async def test_delete_profile(isolated_state_dir, monkeypatch):
    await _start_app_no_socket(monkeypatch)
    deleted: list[str] = []

    async def fake_delete(self, name):
        deleted.append(name)
        return {"type": "profile.deleted", "name": name}

    monkeypatch.setattr("blemees_tui.connection.Connection.delete_profile", fake_delete)
    app = BlemeesTuiApp()
    async with app.run_test():
        await app.on_agent_editor_modal_delete_profile(AgentEditorModal.DeleteProfile("mine"))
        assert deleted == ["mine"]
