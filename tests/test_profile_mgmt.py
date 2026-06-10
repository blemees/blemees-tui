"""Profile management flow in the app (#5): save (create/update), delete,
and graceful agent_unavailable."""

from __future__ import annotations

import pytest

textual = pytest.importorskip("textual")

from blemees_tui.app import BlemeesTuiApp  # noqa: E402
from blemees_tui.connection import ConnectionError_  # noqa: E402
from blemees_tui.widgets.modals import NewSessionModal  # noqa: E402


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
        await app.on_new_session_modal_save_profile(NewSessionModal.SaveProfile("mine", spec))
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
        await app.on_new_session_modal_save_profile(
            NewSessionModal.SaveProfile("mine", {"agent": {}})
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
        await app.on_new_session_modal_save_profile(
            NewSessionModal.SaveProfile("bad", {"agent": {}})
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
        await app.on_new_session_modal_delete_profile(NewSessionModal.DeleteProfile("mine"))
        assert deleted == ["mine"]


# ---- picker robustness against hostile profile names (#23) ----------


@pytest.mark.asyncio
async def test_picker_survives_non_identifier_profile_names(isolated_state_dir, monkeypatch):
    # Names like "my.profile" (legal to the daemon, illegal as Textual widget
    # ids) and markup-shaped names must not crash the picker (#23).
    await _start_app_no_socket(monkeypatch)

    async def fetch():
        return [
            {"name": "default", "source": "config"},
            {"name": "my.profile", "source": "dynamic"},
            {"name": "[red]x[/]", "source": "dynamic"},
        ]

    app = BlemeesTuiApp()
    async with app.run_test() as pilot:
        modal = NewSessionModal(fetch)
        await app.push_screen(modal)
        await pilot.pause()
        # All rows mounted, ids are index-based, names map back losslessly.
        from textual.widgets import RadioButton, RadioSet

        radio = modal.query_one("#profiles", RadioSet)
        buttons = list(radio.query(RadioButton))
        assert [b.id for b in buttons] == ["profile-0", "profile-1", "profile-2", "profile-3"]
        assert modal._radio_name("profile-1") == "my.profile"
        assert modal._radio_name("profile-2") == "[red]x[/]"
        # NOTE: RadioSet.pressed_button stays None after a dynamic mount even
        # with value=True until the user interacts — pre-existing quirk,
        # tracked with the selection-UX item on #25. Selection mapping is
        # asserted in test_picker_selection_maps_through_radio_changes.


@pytest.mark.asyncio
async def test_picker_selection_maps_through_radio_changes(isolated_state_dir, monkeypatch):
    await _start_app_no_socket(monkeypatch)

    async def fetch():
        return [{"name": "default", "source": "config"}, {"name": "my.profile", "source": "dynamic"}]

    app = BlemeesTuiApp()
    async with app.run_test() as pilot:
        modal = NewSessionModal(fetch)
        await app.push_screen(modal)
        await pilot.pause()
        from textual.widgets import RadioButton

        modal.query_one("#profile-1", RadioButton).value = True
        await pilot.pause()
        # Selecting the dotted-name profile pre-fills the editor and resolves
        # the right name for Open/Delete.
        assert modal._selected_profile() == "my.profile"
