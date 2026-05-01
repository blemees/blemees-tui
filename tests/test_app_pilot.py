"""Pilot-driven scene tests (spec §17.2).

These don't take true terminal snapshots (that needs the
``pytest-textual-snapshot`` package + golden files). Instead, they drive
the app through Pilot keystrokes and assert observable widget state for
each spec'd scene. The intent: any regression in compose / data flow
gets caught even before the snapshot infrastructure lands.
"""

from __future__ import annotations

import pytest

textual = pytest.importorskip("textual")

from blemees_tui.app import BlemeesTuiApp  # noqa: E402
from blemees_tui.reducer import apply  # noqa: E402
from blemees_tui.state import SessionMode, SessionState  # noqa: E402
from blemees_tui.widgets import (  # noqa: E402
    ChatPaneWidget,
    ComposerWidget,
    ConnectionBanner,
    FooterStatusWidget,
)


@pytest.fixture(autouse=True)
def _no_socket(monkeypatch):
    async def _noop(self):
        return

    monkeypatch.setattr("blemees_tui.connection.Connection.start", _noop)
    monkeypatch.setattr("blemees_tui.connection.Connection.stop", _noop)


@pytest.mark.asyncio
async def test_scene_empty_state(isolated_state_dir):
    app = BlemeesTuiApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        chat = app.query_one("#chat", ChatPaneWidget)
        # No active session → empty-state placeholder mounted.
        assert chat._empty_state is not None


@pytest.mark.asyncio
async def test_scene_mid_stream_turn_renders(isolated_state_dir):
    app = BlemeesTuiApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        sess = SessionState(session_id="s1", backend="claude")
        app.state.sessions["s1"] = sess
        app.state.active_session_id = "s1"
        apply(sess, {"type": "agent.user", "session_id": "s1", "seq": 1, "message": {"role": "user", "content": "hi"}})
        apply(sess, {"type": "agent.delta", "session_id": "s1", "seq": 2, "kind": "text", "text": "stream"})
        app._refresh_ui()
        await pilot.pause()
        chat = app.query_one("#chat", ChatPaneWidget)
        assert len(chat._turn_widgets) == 1
        # Composer remains enabled while turn_active so the user can queue
        # the next message — Claude Code style.
        assert sess.turn_active
        composer = app.query_one("#composer", ComposerWidget)
        ta = composer.query_one("#composer-input")
        assert not ta.disabled


@pytest.mark.asyncio
async def test_scene_watch_mode_swaps_composer_for_buttons(isolated_state_dir):
    app = BlemeesTuiApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        sess = SessionState(session_id="w1", backend="claude")
        sess.mode = SessionMode.WATCHING
        sess.owner_pid = 12345
        app.state.sessions["w1"] = sess
        app.state.active_session_id = "w1"
        app._refresh_ui()
        await pilot.pause()
        chat = app.query_one("#chat", ChatPaneWidget)
        # Banner mounted with the watching state.
        assert chat._banner_state == "watching"
        # And both buttons exist.
        button_ids = {b.id for b in chat.query("Button")}
        assert "btn-take-ownership" in button_ids
        assert "btn-stop-watching" in button_ids
        # Composer is disabled (we don't drive the watched session).
        composer = app.query_one("#composer", ComposerWidget)
        ta = composer.query_one("#composer-input")
        assert ta.disabled


@pytest.mark.asyncio
async def test_scene_reconnecting_banner(isolated_state_dir):
    app = BlemeesTuiApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        from blemees_tui.connection import ConnectionStatus

        app._on_connection_status(
            ConnectionStatus(state="reconnecting", attempt=2, next_in_ms=3000, last_error="ECONNREFUSED")
        )
        await pilot.pause()
        banner = app.query_one("#conn-banner", ConnectionBanner)
        rendered = str(banner.render())
        assert "Reconnecting" in rendered
        assert "attempt 2" in rendered


@pytest.mark.asyncio
async def test_scene_auth_failed_renders_inline_bubble(isolated_state_dir):
    app = BlemeesTuiApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        sess = SessionState(session_id="s2", backend="claude")
        app.state.sessions["s2"] = sess
        app.state.active_session_id = "s2"
        app._handle_frame(
            {
                "type": "blemeesd.error",
                "session_id": "s2",
                "code": "auth_failed",
                "message": "session expired",
            }
        )
        await pilot.pause()
        chat = app.query_one("#chat", ChatPaneWidget)
        assert chat._errors_widget is not None
        assert "auth_failed" in str(chat._errors_widget.render())
        # Footer error chip reflects it.
        footer = app.query_one("#footer", FooterStatusWidget)
        footer.update_status()
        assert "1 errors" in str(footer.render())


@pytest.mark.asyncio
async def test_scene_replay_gap_banner(isolated_state_dir):
    app = BlemeesTuiApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        sess = SessionState(session_id="s3", backend="claude")
        app.state.sessions["s3"] = sess
        app.state.active_session_id = "s3"
        app._handle_frame({"type": "blemeesd.replay_gap", "session_id": "s3"})
        await pilot.pause()
        chat = app.query_one("#chat", ChatPaneWidget)
        assert chat._gap_widget is not None


@pytest.mark.asyncio
async def test_scene_event_log_overlay_opens(isolated_state_dir):
    app = BlemeesTuiApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        from blemees_tui.state import EventLogSource

        app.state.event_log.append(EventLogSource.CONNECTION, "hello", "blemeesd/0.9.2")
        app.action_event_log()
        await pilot.pause()
        # The overlay is the topmost screen.
        from blemees_tui.widgets.event_log import EventLogOverlay

        assert isinstance(app.screen, EventLogOverlay)
