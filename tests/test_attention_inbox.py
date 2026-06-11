"""Attention inbox (#22): pure tiering, inbox section, ready-for-you
lifecycle, and the jump-to-attention key."""

from __future__ import annotations

import pytest

textual = pytest.importorskip("textual")

from blemees_tui.app import BlemeesTuiApp  # noqa: E402
from blemees_tui.state import SessionState  # noqa: E402
from blemees_tui.widgets.sidebar import attention_badge, attention_tier  # noqa: E402


async def _start_app_no_socket(monkeypatch):
    async def _noop(self):
        return

    monkeypatch.setattr("blemees_tui.connection.Connection.start", _noop)
    monkeypatch.setattr("blemees_tui.connection.Connection.stop", _noop)


# ---- pure tiering ----------------------------------------------------


def test_attention_tier_ordering():
    blocked = SessionState(session_id="b")
    blocked.needs_attention = True
    ready = SessionState(session_id="r")
    ready.ready_for_you = True
    busy = SessionState(session_id="w")
    busy.turn_active = True
    idle = SessionState(session_id="i")
    assert attention_tier(blocked) == 0
    assert attention_tier(ready) == 1
    assert attention_tier(busy) == 2
    assert attention_tier(idle) == 3
    # Blocked outranks ready even when both flags are set.
    both = SessionState(session_id="x")
    both.needs_attention = True
    both.ready_for_you = True
    assert attention_tier(both) == 0


def test_attention_badge_reasons():
    s = SessionState(session_id="s")
    s.needs_attention = True
    s.attention_reason = "turn_complete"
    assert "turn complete" in attention_badge(s)
    s2 = SessionState(session_id="s2")
    s2.ready_for_you = True
    assert "done" in attention_badge(s2)


def test_attention_badge_escapes_hostile_reason():
    s = SessionState(session_id="s")
    s.needs_attention = True
    s.attention_reason = "[red]x[/]"
    badge = attention_badge(s)
    # The daemon-supplied reason is escaped — brackets are neutralized, so
    # no markup can crash or style-inject the sidebar (#16 discipline).
    assert "\\[red]x" in badge


# ---- app lifecycle + inbox section ----------------------------------


@pytest.mark.asyncio
async def test_background_result_sets_ready_and_viewing_clears(isolated_state_dir, monkeypatch):
    await _start_app_no_socket(monkeypatch)
    app = BlemeesTuiApp()
    async with app.run_test() as pilot:
        app.state.sessions["fg"] = SessionState(session_id="fg")
        app.state.sessions["bg"] = SessionState(session_id="bg")
        app._set_active_session("fg")
        app._handle_frame(
            {"type": "session.result", "session_id": "bg", "seq": 1, "stop_reason": "end_turn"}
        )
        await pilot.pause()
        assert app.state.sessions["bg"].ready_for_you is True
        # The foreground session's results never mark ready_for_you.
        app._handle_frame(
            {"type": "session.result", "session_id": "fg", "seq": 1, "stop_reason": "end_turn"}
        )
        assert app.state.sessions["fg"].ready_for_you is False
        # Viewing consumes the marker.
        app._set_active_session("bg")
        assert app.state.sessions["bg"].ready_for_you is False


@pytest.mark.asyncio
async def test_inbox_section_renders_blocked_above_ready(isolated_state_dir, monkeypatch):
    await _start_app_no_socket(monkeypatch)
    app = BlemeesTuiApp()
    async with app.run_test() as pilot:
        from textual.widgets import Static

        from blemees_tui.widgets.sidebar import SidebarWidget

        ready = SessionState(session_id="ready1")
        ready.ready_for_you = True
        blocked = SessionState(session_id="blocked1")
        blocked.needs_attention = True
        blocked.attention_reason = "permission_pending"
        # Insertion order: ready first — the inbox must still put blocked on top.
        app.state.sessions["ready1"] = ready
        app.state.sessions["blocked1"] = blocked
        sidebar = app.query_one("#sidebar", SidebarWidget)
        sidebar.refresh_sessions()
        await pilot.pause()
        attn = app.query_one("#sidebar-attn")
        assert not attn.has_class("-hidden")
        rows = [str(w.render()) for w in attn.query(Static)]
        assert len(rows) == 2
        assert "blocked1" in rows[0] and "permission pending" in rows[0]
        assert "ready1" in rows[1] and "done" in rows[1]


@pytest.mark.asyncio
async def test_inbox_hidden_when_nothing_needs_attention(isolated_state_dir, monkeypatch):
    await _start_app_no_socket(monkeypatch)
    app = BlemeesTuiApp()
    async with app.run_test() as pilot:
        from blemees_tui.widgets.sidebar import SidebarWidget

        app.state.sessions["calm"] = SessionState(session_id="calm")
        app.query_one("#sidebar", SidebarWidget).refresh_sessions()
        await pilot.pause()
        assert app.query_one("#sidebar-attn").has_class("-hidden")


@pytest.mark.asyncio
async def test_jump_attention_selects_blocked_first(isolated_state_dir, monkeypatch):
    await _start_app_no_socket(monkeypatch)
    app = BlemeesTuiApp()
    async with app.run_test() as pilot:
        ready = SessionState(session_id="ready1")
        ready.ready_for_you = True
        blocked = SessionState(session_id="blocked1")
        blocked.needs_attention = True
        app.state.sessions["ready1"] = ready
        app.state.sessions["blocked1"] = blocked
        app.action_jump_attention()
        await pilot.pause()
        assert app.state.active_session_id == "blocked1"
        # Blocked resolved → next jump goes to the ready session.
        blocked.needs_attention = False
        app.action_jump_attention()
        await pilot.pause()
        assert app.state.active_session_id == "ready1"


@pytest.mark.asyncio
async def test_jump_attention_with_calm_fleet_notifies(isolated_state_dir, monkeypatch):
    await _start_app_no_socket(monkeypatch)
    app = BlemeesTuiApp()
    async with app.run_test() as pilot:
        app.state.sessions["calm"] = SessionState(session_id="calm")
        app.action_jump_attention()
        await pilot.pause()
        assert app.state.active_session_id is None  # unchanged, no crash
