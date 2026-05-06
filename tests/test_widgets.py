"""Widget unit tests — pure logic only.

Pilot-driven snapshot tests live in ``test_app_pilot.py`` (M5).
"""

from __future__ import annotations

import pytest

textual = pytest.importorskip("textual")
from textual.app import App  # noqa: E402

from blemees_tui.reducer import apply  # noqa: E402
from blemees_tui.state import AppState, RateLimitsNotice, SessionState  # noqa: E402
from blemees_tui.widgets.banner import ConnectionBanner  # noqa: E402
from blemees_tui.widgets.chat_pane import ChatPaneWidget, _TurnBlock  # noqa: E402
from blemees_tui.widgets.composer import ComposerWidget  # noqa: E402
from blemees_tui.widgets.footer import FooterStatusWidget  # noqa: E402


class _ComposerOnlyApp(App):
    def compose(self):
        yield ComposerWidget(id="c")


class _ChatOnlyApp(App):
    def compose(self):
        yield ChatPaneWidget(id="chat")


@pytest.mark.asyncio
async def test_chat_pane_incremental_mounts_one_widget_per_turn():
    """ChatPane should mount exactly one ``_TurnBlock`` per turn and reuse
    them when re-rendering — no full rebuild on every frame."""
    app = _ChatOnlyApp()
    async with app.run_test() as pilot:
        chat = app.query_one("#chat", ChatPaneWidget)
        sess = SessionState(session_id="s1")
        # First turn
        apply(
            sess,
            {
                "type": "agent.user",
                "session_id": "s1",
                "seq": 1,
                "message": {"role": "user", "content": "hi"},
            },
        )
        chat.show_session(sess)
        await pilot.pause()
        first_blocks = list(chat.query(_TurnBlock))
        assert len(first_blocks) == 1
        # A streaming delta on the same turn should NOT add a widget.
        apply(
            sess,
            {"type": "agent.delta", "session_id": "s1", "seq": 2, "kind": "text", "text": "hello"},
        )
        chat.show_session(sess)
        await pilot.pause()
        same_blocks = list(chat.query(_TurnBlock))
        assert len(same_blocks) == 1
        assert same_blocks[0] is first_blocks[0]
        # A second turn should add exactly one new widget.
        apply(sess, {"type": "agent.result", "session_id": "s1", "seq": 3, "subtype": "success"})
        apply(
            sess,
            {
                "type": "agent.user",
                "session_id": "s1",
                "seq": 4,
                "message": {"role": "user", "content": "more"},
            },
        )
        chat.show_session(sess)
        await pilot.pause()
        two_blocks = list(chat.query(_TurnBlock))
        assert len(two_blocks) == 2


class _FooterOnlyApp(App):
    def __init__(self, state: AppState):
        super().__init__()
        self._state = state

    def compose(self):
        yield FooterStatusWidget(self._state, id="footer")


class _BannerOnlyApp(App):
    def compose(self):
        yield ConnectionBanner(id="b")


@pytest.mark.asyncio
async def test_connection_banner_states():
    app = _BannerOnlyApp()
    async with app.run_test() as pilot:
        b = app.query_one("#b", ConnectionBanner)
        # connected → hidden
        b.set_connection(state="connected", attempt=0, next_in_ms=0, last_error="")
        await pilot.pause()
        assert b.has_class("-hidden")
        # reconnecting → visible w/ attempt + next-in
        b.set_connection(state="reconnecting", attempt=3, next_in_ms=2500, last_error="boom")
        await pilot.pause()
        assert not b.has_class("-hidden")
        rendered = str(b.render())
        assert "attempt 3" in rendered
        assert "next in 2s" in rendered
        # latched fatal stays even when set_connection(connected)
        b.set_fatal("slow_consumer — fell behind")
        b.set_connection(state="connected", attempt=0, next_in_ms=0, last_error="")
        await pilot.pause()
        assert "slow_consumer" in str(b.render())
        assert b.has_class("-fatal")
        # clear → connected hides again
        b.clear_fatal()
        b.set_connection(state="connected", attempt=0, next_in_ms=0, last_error="")
        await pilot.pause()
        assert b.has_class("-hidden")


@pytest.mark.asyncio
async def test_footer_renders_errors_and_rate_chips():
    state = AppState()
    sess = SessionState(session_id="s")
    sess.pending_errors.append({"code": "auth_failed", "message": "x"})
    state.sessions["s"] = sess
    state.rate_limits = RateLimitsNotice(level="warn", text="resets in 4m", session_id="s")
    state.daemon.daemon = "blemees-agentd/0.9.2"
    state.daemon.backends = {"claude": "2.1"}
    app = _FooterOnlyApp(state)
    async with app.run_test() as pilot:
        footer = app.query_one("#footer", FooterStatusWidget)
        footer.update_status()
        await pilot.pause()
        rendered = str(footer.render())
        assert "1 errors" in rendered
        assert "resets in 4m" in rendered
        assert "blemees-agentd/0.9.2" in rendered


@pytest.mark.asyncio
async def test_chat_pane_renders_pending_errors():
    """Errors pushed via the reducer should appear inline (§9.8)."""
    app = _ChatOnlyApp()
    async with app.run_test() as pilot:
        chat = app.query_one("#chat", ChatPaneWidget)
        sess = SessionState(session_id="s1")
        apply(
            sess,
            {
                "type": "agent.error",
                "session_id": "s1",
                "code": "auth_failed",
                "message": "Claude session not authenticated",
            },
        )
        chat.show_session(sess)
        await pilot.pause()
        assert chat._errors_widget is not None
        markup = str(chat._errors_widget.render())
        assert "auth_failed" in markup


@pytest.mark.asyncio
async def test_chat_pane_replay_gap_banner():
    """When state.replay_gap is True, a banner is mounted at the top."""
    app = _ChatOnlyApp()
    async with app.run_test() as pilot:
        chat = app.query_one("#chat", ChatPaneWidget)
        sess = SessionState(session_id="s1")
        apply(sess, {"type": "agent.replay_gap", "session_id": "s1"})
        chat.show_session(sess)
        await pilot.pause()
        assert chat._gap_widget is not None


@pytest.mark.asyncio
async def test_composer_recall_cycles_history():
    app = _ComposerOnlyApp()
    async with app.run_test() as pilot:
        composer = app.query_one("#c", ComposerWidget)
        composer.set_recall_history(["first", "second", "third"])
        await pilot.pause()
        # Up from empty → most recent
        composer._recall_step(-1)
        assert composer.query_one("#composer-input").text == "third"
        # Up again → previous
        composer._recall_step(-1)
        assert composer.query_one("#composer-input").text == "second"
        # Down → forward
        composer._recall_step(+1)
        assert composer.query_one("#composer-input").text == "third"
        # Down again past end → clear
        composer._recall_step(+1)
        assert composer.query_one("#composer-input").text == ""
        assert composer._recall_index is None
