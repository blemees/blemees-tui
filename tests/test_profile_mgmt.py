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
        # The first profile is *really* selected on a fresh modal (#29):
        # post-mount assignment registers with the RadioSet, unlike the old
        # construction-time value=True which left pressed_button None.
        await pilot.pause()
        assert modal._selected_profile() == "default"


@pytest.mark.asyncio
async def test_picker_selection_maps_through_radio_changes(isolated_state_dir, monkeypatch):
    await _start_app_no_socket(monkeypatch)

    async def fetch():
        return [
            {"name": "default", "source": "config"},
            {"name": "my.profile", "source": "dynamic"},
        ]

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


# ---- modal scrolls so the editor and Save stay reachable (#24) -------


@pytest.mark.asyncio
async def test_modal_scrolls_save_button_into_view_on_small_terminal(
    isolated_state_dir, monkeypatch
):
    await _start_app_no_socket(monkeypatch)

    async def fetch():
        return [{"name": "default", "source": "config"}]

    app = BlemeesTuiApp()
    async with app.run_test(size=(100, 24)) as pilot:
        modal = NewSessionModal(fetch)
        await app.push_screen(modal)
        await pilot.pause()
        from textual.containers import VerticalScroll
        from textual.widgets import Button, Collapsible

        box = modal.query_one("#new-session-box", VerticalScroll)
        modal.query_one("#editor", Collapsible).collapsed = False
        await pilot.pause()
        save = modal.query_one("#save", Button)
        save.focus()
        await pilot.pause()
        # The body is a scroll container and focusing the (initially
        # off-screen) Save button scrolls it toward view. Before the fix the
        # box was a plain Vertical: allow_vertical_scroll False, offset
        # pinned at 0, Save clipped with no way to reach it. (No exact
        # row-geometry assert — scrollbar metrics differ ±2 across
        # platforms; the scroll-happened signal is the regression guard.)
        assert save.has_focus
        assert box.allow_vertical_scroll
        assert box.scroll_offset.y > 0


# ---- deterministic selection + no silent no-ops (#29) ----------------


@pytest.mark.asyncio
async def test_fresh_modal_prefills_editor_from_initial_selection(isolated_state_dir, monkeypatch):
    await _start_app_no_socket(monkeypatch)

    async def fetch():
        return [{"name": "default", "source": "config", "agents": [{"agent": "claude-agent-acp"}]}]

    app = BlemeesTuiApp()
    async with app.run_test() as pilot:
        modal = NewSessionModal(fetch)
        await app.push_screen(modal)
        await pilot.pause()
        await pilot.pause()
        from textual.widgets import Input

        # The initial selection fires Changed, so the editor reflects it.
        assert modal._selected_profile() == "default"
        assert modal.query_one("#p-name", Input).value == "default"
        assert modal.query_one("#p-agent_command", Input).value == "claude-agent-acp"


@pytest.mark.asyncio
async def test_open_with_no_selection_warns_instead_of_silent_noop(isolated_state_dir, monkeypatch):
    await _start_app_no_socket(monkeypatch)

    async def fetch():
        return []  # only the "new profile" sentinel row — nothing selectable

    app = BlemeesTuiApp()
    async with app.run_test() as pilot:
        modal = NewSessionModal(fetch)
        await app.push_screen(modal)
        await pilot.pause()
        assert modal._selected_profile() == ""
        modal._do_open()
        await pilot.pause()
        # Modal stays open and the user is told why nothing happened.
        assert app.screen is modal
        modal._do_delete()
        await pilot.pause()
        assert app.screen is modal


# ---- agent picker within a multi-agent profile (#37) -----------------


def _multi_agent_rows():
    return [
        {
            "name": "dev",
            "source": "config",
            "agents": [
                {"name": "claude", "agent": "claude-agent-acp"},
                {"name": "cursor", "agent": "cursor-agent"},
                {"name": "bad[name]", "agent": "junie-acp"},
            ],
        },
        {
            "name": "solo",
            "source": "config",
            "agents": [{"name": "default", "agent": "claude-agent-acp"}],
        },
    ]


@pytest.mark.asyncio
async def test_multi_agent_profile_shows_picker_and_opens_under_chosen_agent(
    isolated_state_dir, monkeypatch
):
    await _start_app_no_socket(monkeypatch)
    opened: list[dict] = []

    async def fake_open(self, sid, **kw):
        opened.append(kw)
        return {"type": "session.open_ok"}

    monkeypatch.setattr("blemees_tui.connection.Connection.open_session", fake_open)

    async def fetch():
        return _multi_agent_rows()

    app = BlemeesTuiApp()
    async with app.run_test() as pilot:
        modal = NewSessionModal(fetch)
        await app.push_screen(modal)
        await pilot.pause()
        await pilot.pause()
        from textual.widgets import RadioButton, RadioSet

        agents = modal.query_one("#agents", RadioSet)
        # Visible, index-based ids, hostile name escaped without crashing.
        assert not agents.has_class("hidden")
        ids = [b.id for b in agents.query(RadioButton)]
        assert ids == ["agent-0", "agent-1", "agent-2"]
        await pilot.pause()  # let the post-mount agent press settle
        assert modal._selected_agent() == "claude"  # first agent pre-selected
        # Pick the second agent and open.
        modal.query_one("#agent-1", RadioButton).value = True
        await pilot.pause()
        assert modal._selected_agent() == "cursor"
        modal._do_open()
        await pilot.pause()
        await pilot.pause()
        assert opened and opened[0]["profile"] == "dev"
        assert opened[0]["agent"] == "cursor"


@pytest.mark.asyncio
async def test_single_agent_profile_hides_picker_and_sends_no_agent(
    isolated_state_dir, monkeypatch
):
    await _start_app_no_socket(monkeypatch)
    opened: list[dict] = []

    async def fake_open(self, sid, **kw):
        opened.append(kw)
        return {"type": "session.open_ok"}

    monkeypatch.setattr("blemees_tui.connection.Connection.open_session", fake_open)

    async def fetch():
        return [_multi_agent_rows()[1]]  # just "solo"

    app = BlemeesTuiApp()
    async with app.run_test() as pilot:
        modal = NewSessionModal(fetch)
        await app.push_screen(modal)
        await pilot.pause()
        await pilot.pause()
        from textual.widgets import RadioSet

        assert modal.query_one("#agents", RadioSet).has_class("hidden")
        assert modal._selected_agent() is None
        modal._do_open()
        await pilot.pause()
        await pilot.pause()
        assert opened and opened[0]["profile"] == "solo"
        assert opened[0]["agent"] is None


@pytest.mark.asyncio
async def test_switching_to_new_profile_clears_agent_picker(isolated_state_dir, monkeypatch):
    await _start_app_no_socket(monkeypatch)

    async def fetch():
        return _multi_agent_rows()

    app = BlemeesTuiApp()
    async with app.run_test() as pilot:
        modal = NewSessionModal(fetch)
        await app.push_screen(modal)
        await pilot.pause()
        await pilot.pause()
        from textual.widgets import RadioButton, RadioSet

        assert not modal.query_one("#agents", RadioSet).has_class("hidden")
        # Select the "＋ New profile…" sentinel (last row) — picker hides.
        last = len(modal._radio_names) - 1
        modal.query_one(f"#profile-{last}", RadioButton).value = True
        await pilot.pause()
        assert modal.query_one("#agents", RadioSet).has_class("hidden")
        assert modal._selected_agent() is None
