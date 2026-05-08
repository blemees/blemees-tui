"""Widget unit tests — pure logic only.

Pilot-driven snapshot tests live in ``test_app_pilot.py`` (M5).
"""

from __future__ import annotations

import pytest

textual = pytest.importorskip("textual")
from textual.app import App  # noqa: E402

from blemees_tui import __version__ as _TUI_VERSION  # noqa: E402
from blemees_tui.reducer import apply  # noqa: E402
from blemees_tui.state import AppState, RateLimitsNotice, SessionState  # noqa: E402
from blemees_tui.widgets.banner import ConnectionBanner  # noqa: E402
from blemees_tui.state import ToolUseBlock  # noqa: E402
from blemees_tui.widgets.chat_pane import (  # noqa: E402
    ChatPaneWidget,
    _format_todowrite_summary,
    _TurnBlock,
)
from blemees_tui.widgets.composer import ComposerWidget  # noqa: E402
from blemees_tui.widgets.footer import FooterStatusWidget  # noqa: E402
from blemees_tui.widgets.sidebar import SidebarWidget  # noqa: E402
from blemees_tui.widgets.todo_panel import TodoPanel, _latest_todos  # noqa: E402
from blemees_tui.widgets.turn_status import TurnStatusBar  # noqa: E402


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


class _SidebarOnlyApp(App):
    def __init__(self, state: AppState):
        super().__init__()
        self._state = state

    def compose(self):
        yield SidebarWidget(self._state, id="sidebar")


class _TurnStatusOnlyApp(App):
    def __init__(self, state: AppState):
        super().__init__()
        self._state = state

    def compose(self):
        yield TurnStatusBar(self._state, id="turn-status")


@pytest.mark.asyncio
async def test_turn_status_shows_total_turns_on_right():
    state = AppState()
    sess = SessionState(session_id="s")
    # Two completed turns — the bar reports the count regardless of
    # whether a turn is currently in flight.
    apply(
        sess,
        {
            "type": "agent.user",
            "session_id": "s",
            "seq": 1,
            "message": {"role": "user", "content": "hi"},
        },
    )
    apply(sess, {"type": "agent.result", "session_id": "s", "seq": 2, "subtype": "success"})
    apply(
        sess,
        {
            "type": "agent.user",
            "session_id": "s",
            "seq": 3,
            "message": {"role": "user", "content": "again"},
        },
    )
    apply(sess, {"type": "agent.result", "session_id": "s", "seq": 4, "subtype": "success"})
    state.sessions["s"] = sess
    state.active_session_id = "s"
    app = _TurnStatusOnlyApp(state)
    async with app.run_test() as pilot:
        bar = app.query_one("#turn-status", TurnStatusBar)
        bar.update_status()
        await pilot.pause()
        right = str(bar.query_one("#turn-status-right").render())
        assert "2 turns" in right
        # No turn in flight — left side stays empty.
        left = str(bar.query_one("#turn-status-left").render())
        assert "tok" not in left


@pytest.mark.asyncio
async def test_turn_status_shows_locked_turn_summary_after_result():
    """Once ``agent.result`` lands with a duration + usage, the live
    spinner is replaced by the same ``Xs · ↑in · ↓out`` summary the
    chat-pane divider shows."""
    state = AppState()
    sess = SessionState(session_id="s")
    apply(
        sess,
        {
            "type": "agent.user",
            "session_id": "s",
            "seq": 1,
            "message": {"role": "user", "content": "go"},
        },
    )
    apply(
        sess,
        {
            "type": "agent.result",
            "session_id": "s",
            "seq": 2,
            "subtype": "success",
            "duration_ms": 1500,
            "usage": {"input_tokens": 1234, "output_tokens": 567},
        },
    )
    state.sessions["s"] = sess
    state.active_session_id = "s"
    assert not sess.turn_active
    app = _TurnStatusOnlyApp(state)
    async with app.run_test() as pilot:
        bar = app.query_one("#turn-status", TurnStatusBar)
        bar.update_status()
        await pilot.pause()
        left = str(bar.query_one("#turn-status-left").render())
        assert "1.5s" in left
        assert "↑1234" in left
        assert "↓567" in left
        # No spinner / live token estimate after the turn locks.
        assert "tok" not in left


@pytest.mark.asyncio
async def test_turn_status_shows_live_seconds_and_tokens_during_turn():
    state = AppState()
    sess = SessionState(session_id="s")
    apply(
        sess,
        {
            "type": "agent.user",
            "session_id": "s",
            "seq": 1,
            "message": {"role": "user", "content": "hi"},
        },
    )
    # Stream a delta — turn_active flips True and the in-flight turn
    # accumulates text the bar can estimate tokens from.
    apply(
        sess,
        {
            "type": "agent.delta",
            "session_id": "s",
            "seq": 2,
            "kind": "text",
            "text": "hello world " * 10,
        },
    )
    state.sessions["s"] = sess
    state.active_session_id = "s"
    assert sess.turn_active
    app = _TurnStatusOnlyApp(state)
    async with app.run_test() as pilot:
        bar = app.query_one("#turn-status", TurnStatusBar)
        bar.update_status()
        await pilot.pause()
        left = str(bar.query_one("#turn-status-left").render())
        assert "tok" in left
        assert "s" in left  # elapsed seconds


def _render_to_text(renderable) -> str:
    """Capture a Rich renderable to a plain string for assertion."""
    from rich.console import Console

    console = Console(record=True, width=120, color_system=None, legacy_windows=False)
    console.print(renderable)
    return console.export_text()


def test_format_todowrite_summary_collapses_to_one_liner():
    """Chat transcript only shows a brief marker — the full checklist
    lives in the TodoPanel above the composer."""
    todos = [
        {"content": "ship feature", "activeForm": "Shipping feature", "status": "completed"},
        {"content": "write tests", "activeForm": "Writing tests", "status": "in_progress"},
        {"content": "polish docs", "activeForm": "Polishing docs", "status": "pending"},
    ]
    rendered = _format_todowrite_summary("TodoWrite", {"todos": todos})
    assert rendered is not None
    assert "TodoWrite" in rendered
    assert "1/3" in rendered
    # No checklist glyphs / item content leaks into the chat pane summary.
    for needle in ("☑", "◐", "☐", "ship feature", "Writing tests", "polish docs"):
        assert needle not in rendered


def test_format_todowrite_summary_returns_none_for_other_tools():
    assert _format_todowrite_summary("Bash", {"command": "ls"}) is None


def test_latest_todos_walks_session_in_reverse():
    """``_latest_todos`` returns the most recent TodoWrite snapshot — newest
    turn wins, and within a turn the latest block wins."""
    sess = SessionState(session_id="s")
    apply(sess, {"type": "agent.user", "session_id": "s", "seq": 1, "message": {"role": "user", "content": "go"}})
    # Two TodoWrite calls within turn 1 — the second should win.
    apply(
        sess,
        {
            "type": "agent.tool_use",
            "session_id": "s",
            "seq": 2,
            "tool_use_id": "tu1",
            "name": "TodoWrite",
            "input": {"todos": [{"content": "old", "status": "pending"}]},
        },
    )
    apply(
        sess,
        {
            "type": "agent.tool_use",
            "session_id": "s",
            "seq": 3,
            "tool_use_id": "tu2",
            "name": "TodoWrite",
            "input": {"todos": [{"content": "new", "status": "in_progress"}]},
        },
    )
    todos = _latest_todos(sess)
    assert todos is not None
    assert todos[0]["content"] == "new"


def test_latest_todos_returns_none_when_no_todowrite_calls():
    sess = SessionState(session_id="s")
    apply(sess, {"type": "agent.user", "session_id": "s", "seq": 1, "message": {"role": "user", "content": "hi"}})
    assert _latest_todos(sess) is None


class _TodoPanelOnlyApp(App):
    def __init__(self, state: AppState):
        super().__init__()
        self._state = state

    def compose(self):
        yield TodoPanel(self._state, id="todos")


@pytest.mark.asyncio
async def test_todo_panel_renders_checklist_for_active_session():
    state = AppState()
    sess = SessionState(session_id="s")
    apply(sess, {"type": "agent.user", "session_id": "s", "seq": 1, "message": {"role": "user", "content": "go"}})
    apply(
        sess,
        {
            "type": "agent.tool_use",
            "session_id": "s",
            "seq": 2,
            "tool_use_id": "tu1",
            "name": "TodoWrite",
            "input": {
                "todos": [
                    {"content": "ship feature", "status": "completed"},
                    {"content": "write tests", "activeForm": "Writing tests", "status": "in_progress"},
                    {"content": "polish docs", "status": "pending"},
                ]
            },
        },
    )
    state.sessions["s"] = sess
    state.active_session_id = "s"
    app = _TodoPanelOnlyApp(state)
    async with app.run_test() as pilot:
        panel = app.query_one("#todos", TodoPanel)
        panel.update_status()
        await pilot.pause()
        rendered = str(panel.render())
        assert not panel.has_class("-hidden")
        # All three glyphs render.
        assert "☑" in rendered
        assert "◐" in rendered
        assert "☐" in rendered
        # in_progress uses activeForm; completed/pending use content.
        assert "Writing tests" in rendered
        assert "ship feature" in rendered
        assert "polish docs" in rendered


@pytest.mark.asyncio
async def test_todo_panel_hidden_when_no_todos():
    state = AppState()
    sess = SessionState(session_id="s")
    state.sessions["s"] = sess
    state.active_session_id = "s"
    app = _TodoPanelOnlyApp(state)
    async with app.run_test() as pilot:
        panel = app.query_one("#todos", TodoPanel)
        panel.update_status()
        await pilot.pause()
        assert panel.has_class("-hidden")


@pytest.mark.asyncio
async def test_todo_panel_hidden_when_every_item_completed():
    """Once the agent ticks every item off, the working list collapses
    so the chat pane reclaims the row."""
    state = AppState()
    sess = SessionState(session_id="s")
    apply(sess, {"type": "agent.user", "session_id": "s", "seq": 1, "message": {"role": "user", "content": "go"}})
    apply(
        sess,
        {
            "type": "agent.tool_use",
            "session_id": "s",
            "seq": 2,
            "tool_use_id": "tu1",
            "name": "TodoWrite",
            "input": {
                "todos": [
                    {"content": "step one", "status": "completed"},
                    {"content": "step two", "status": "completed"},
                ]
            },
        },
    )
    state.sessions["s"] = sess
    state.active_session_id = "s"
    app = _TodoPanelOnlyApp(state)
    async with app.run_test() as pilot:
        panel = app.query_one("#todos", TodoPanel)
        panel.update_status()
        await pilot.pause()
        assert panel.has_class("-hidden")


@pytest.mark.asyncio
async def test_footer_renders_errors_versions_and_rate_chips():
    state = AppState()
    sess = SessionState(session_id="s", backend="claude")
    sess.pending_errors.append({"code": "auth_failed", "message": "x"})
    state.sessions["s"] = sess
    state.active_session_id = "s"
    state.connection_status = "connected"
    state.rate_limits = RateLimitsNotice(level="warn", text="resets in 4m", session_id="s")
    state.daemon.daemon = "blemees-agentd/0.9.2"
    state.daemon.backends = {"claude": "2.1"}
    app = _FooterOnlyApp(state)
    async with app.run_test() as pilot:
        footer = app.query_one("#footer", FooterStatusWidget)
        footer.update_status()
        await pilot.pause()
        status = str(footer.query_one("#footer-status").render())
        info = str(footer.query_one("#footer-info").render())
        errors = str(footer.query_one("#footer-errors").render())
        # Connection state lives in the sidebar-aligned status section.
        assert "connected" in status
        # blemees + active backend versions and rate notice align with the
        # chat / text-area column.
        assert "blemees" in info
        assert _TUI_VERSION in info
        assert "claude" in info
        assert "2.1" in info
        assert "resets in 4m" in info
        # Errors are pinned to the right.
        assert "1 errors" in errors
        assert "1 errors" not in info
        # Turn count still lives in TurnStatusBar above the composer.
        for region in (status, info, errors):
            assert "turns" not in region


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
