"""Widget unit tests — pure logic only.

Pilot-driven snapshot tests live in ``test_app_pilot.py`` (M5).
"""

from __future__ import annotations

import pytest

textual = pytest.importorskip("textual")
from textual.app import App  # noqa: E402

from blemees_tui import __version__ as _TUI_VERSION  # noqa: E402
from blemees_tui.reducer import apply, apply_user_prompt  # noqa: E402
from blemees_tui.state import (  # noqa: E402
    AppState,
    RateLimitsNotice,
    SessionState,  # noqa: E402
)
from blemees_tui.widgets.banner import ConnectionBanner  # noqa: E402
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
        apply_user_prompt(sess, "hi")
        chat.show_session(sess)
        await pilot.pause()
        first_blocks = list(chat.query(_TurnBlock))
        assert len(first_blocks) == 1
        # A streaming chunk on the same turn should NOT add a widget.
        apply(
            sess,
            {
                "type": "session.update",
                "session_id": "s1",
                "seq": 2,
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "hello"},
                },
            },
        )
        chat.show_session(sess)
        await pilot.pause()
        same_blocks = list(chat.query(_TurnBlock))
        assert len(same_blocks) == 1
        assert same_blocks[0] is first_blocks[0]
        # A second turn should add exactly one new widget.
        apply(
            sess,
            {"type": "session.result", "session_id": "s1", "seq": 3, "stop_reason": "end_turn"},
        )
        apply_user_prompt(sess, "more")
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
async def test_turn_status_shows_model_on_left_and_turns_on_right():
    state = AppState()
    sess = SessionState(session_id="s", model="claude-sonnet-4-5")
    # Two completed turns — the bar reports the count regardless of
    # whether a turn is currently in flight.
    apply_user_prompt(sess, "hi")
    apply(sess, {"type": "session.result", "session_id": "s", "seq": 2, "stop_reason": "end_turn"})
    apply_user_prompt(sess, "again")
    apply(sess, {"type": "session.result", "session_id": "s", "seq": 4, "stop_reason": "end_turn"})
    state.sessions["s"] = sess
    state.active_session_id = "s"
    app = _TurnStatusOnlyApp(state)
    async with app.run_test() as pilot:
        bar = app.query_one("#turn-status", TurnStatusBar)
        bar.update_status()
        await pilot.pause()
        # Model name occupies the left when idle (no in-flight turn).
        left = str(bar.query_one("#turn-status-left").render())
        assert "claude-sonnet-4-5" in left
        assert "tok" not in left  # no live timer
        # Turn count lives on the right; model is not duplicated there.
        right = str(bar.query_one("#turn-status-right").render())
        assert "2 turns" in right
        assert "claude-sonnet-4-5" not in right


@pytest.mark.asyncio
async def test_turn_status_left_is_empty_when_session_has_no_model():
    state = AppState()
    sess = SessionState(session_id="s")  # no model set
    state.sessions["s"] = sess
    state.active_session_id = "s"
    app = _TurnStatusOnlyApp(state)
    async with app.run_test() as pilot:
        bar = app.query_one("#turn-status", TurnStatusBar)
        bar.update_status()
        await pilot.pause()
        left = str(bar.query_one("#turn-status-left").render())
        assert left.strip() == ""
        right = str(bar.query_one("#turn-status-right").render())
        assert "0 turns" in right


@pytest.mark.asyncio
async def test_turn_status_live_timer_replaces_model_on_left_during_turn():
    state = AppState()
    sess = SessionState(session_id="s", model="claude-sonnet-4-5")
    apply_user_prompt(sess, "hi")
    # Stream a chunk — turn_active stays True and the in-flight turn
    # accumulates text the bar can estimate tokens from.
    apply(
        sess,
        {
            "type": "session.update",
            "session_id": "s",
            "seq": 2,
            "update": {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": "hello world " * 10},
            },
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
        # The live timer takes over the slot — model name is hidden while
        # the turn is in flight.
        assert "claude-sonnet-4-5" not in left


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


def test_latest_todos_returns_session_plan():
    """``_latest_todos`` surfaces the session's ACP plan entries (#2)."""
    sess = SessionState(session_id="s")
    apply(
        sess,
        {
            "type": "session.update",
            "session_id": "s",
            "seq": 1,
            "update": {
                "sessionUpdate": "plan",
                "entries": [{"content": "step one", "status": "in_progress", "priority": "high"}],
            },
        },
    )
    todos = _latest_todos(sess)
    assert todos is not None
    assert todos[0]["content"] == "step one"


def test_latest_todos_returns_none_when_no_plan():
    assert _latest_todos(SessionState(session_id="s")) is None


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
    apply(
        sess,
        {
            "type": "session.update",
            "session_id": "s",
            "seq": 1,
            "update": {
                "sessionUpdate": "plan",
                "entries": [
                    {"content": "ship feature", "status": "completed"},
                    {"content": "write tests", "status": "in_progress"},
                    {"content": "polish docs", "status": "pending"},
                ],
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
        # All three status glyphs render.
        assert "☑" in rendered
        assert "◐" in rendered
        assert "☐" in rendered
        assert "ship feature" in rendered
        assert "write tests" in rendered
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
    state.daemon.daemon = "blemees-agentd/0.11.0"
    state.daemon.agents = {"claude-agent-acp": "2.1"}
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
        # The active session's profile name ("claude") is shown in the info slot.
        assert "claude" in info
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
                "type": "session.error",
                "session_id": "s1",
                "code": "auth_required",
                "message": "agent not authenticated",
            },
        )
        chat.show_session(sess)
        await pilot.pause()
        assert chat._errors_widget is not None
        markup = str(chat._errors_widget.render())
        assert "auth_required" in markup


@pytest.mark.asyncio
async def test_chat_pane_replay_gap_banner():
    """When state.replay_gap is True, a banner is mounted at the top."""
    app = _ChatOnlyApp()
    async with app.run_test() as pilot:
        chat = app.query_one("#chat", ChatPaneWidget)
        sess = SessionState(session_id="s1")
        apply(sess, {"type": "replay_gap", "session_id": "s1"})
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


def _sidebar_text(app) -> str:
    box = app.query_one("#sidebar-tree")
    return "\n".join(str(child.render()) for child in box.children)


@pytest.mark.asyncio
async def test_sidebar_shows_full_agent_roster_including_sessionless():
    """The sidebar lists every agent in the active profile's roster, even
    those with no sessions, and shows a live count for those that do."""
    state = AppState()
    state.daemon.active_profile = "default"
    state.daemon.agent_roster = [
        {"name": "developer", "model": None},
        {"name": "architect", "model": None},
        {"name": "tester", "model": None},
    ]
    # One live session on `developer`; architect/tester have none.
    sess = SessionState(session_id="s1", agent="developer", backend="default")
    state.sessions["s1"] = sess
    state.active_session_id = "s1"

    app = _SidebarOnlyApp(state)
    async with app.run_test() as pilot:
        sidebar = app.query_one("#sidebar", SidebarWidget)
        sidebar.refresh_sessions(active_id="s1")
        await pilot.pause()
        rendered = _sidebar_text(app)
        # All three agents are listed, regardless of session presence.
        assert "developer" in rendered
        assert "architect" in rendered
        assert "tester" in rendered
        # developer has one live session → count shown.
        assert "(1)" in rendered


@pytest.mark.asyncio
async def test_sidebar_nests_sessions_under_their_agent():
    """Sessions render indented beneath their agent header, not as a
    separate flat list."""
    state = AppState()
    state.daemon.active_profile = "default"
    state.daemon.agent_roster = [
        {"name": "developer", "model": None},
        {"name": "architect", "model": None},
    ]
    state.sessions["s1"] = SessionState(
        session_id="s1", agent="developer", backend="default", title="build-api"
    )
    state.sessions["s2"] = SessionState(
        session_id="s2", agent="architect", backend="default", title="design-doc"
    )
    state.active_session_id = "s1"

    app = _SidebarOnlyApp(state)
    async with app.run_test() as pilot:
        sidebar = app.query_one("#sidebar", SidebarWidget)
        sidebar.refresh_sessions(active_id="s1")
        await pilot.pause()
        box = app.query_one("#sidebar-tree")
        rows = [(type(c).__name__, str(c.render()), c.classes) for c in box.children]
        # developer header is immediately followed by its session row.
        headers = [i for i, (_, txt, cls) in enumerate(rows) if "agent-header" in cls]
        assert headers, "expected agent headers"
        dev_idx = next(i for i, (_, txt, _) in enumerate(rows) if "developer" in txt)
        assert "build-api" in rows[dev_idx + 1][1]
        assert "session" in rows[dev_idx + 1][2]


@pytest.mark.asyncio
async def test_sidebar_restricts_sessions_to_active_profile():
    """Only sessions whose backend matches the active profile are shown,
    and their numeric index is scoped to that visible set."""
    state = AppState()
    state.daemon.active_profile = "default"
    state.daemon.agent_roster = [{"name": "developer", "model": None}]
    # In-profile session, plus one from another profile that must be hidden.
    state.sessions["s1"] = SessionState(
        session_id="s1", agent="developer", backend="default", title="mine"
    )
    state.sessions["s2"] = SessionState(
        session_id="s2", agent="developer", backend="other", title="theirs"
    )

    # Selection scope agrees with the display.
    assert state.visible_session_ids() == ["s1"]

    app = _SidebarOnlyApp(state)
    async with app.run_test() as pilot:
        sidebar = app.query_one("#sidebar", SidebarWidget)
        sidebar.refresh_sessions(active_id="s1")
        await pilot.pause()
        rendered = _sidebar_text(app)
        assert "mine" in rendered
        assert "theirs" not in rendered


@pytest.mark.asyncio
async def test_sidebar_shows_all_sessions_before_profile_known():
    """Before connect (no active_profile) every session is visible — the
    pre-connect behavior is preserved."""
    state = AppState()
    state.sessions["s1"] = SessionState(session_id="s1", agent="developer", title="a")
    state.sessions["s2"] = SessionState(session_id="s2", agent="architect", title="b")
    assert state.visible_session_ids() == ["s1", "s2"]

    app = _SidebarOnlyApp(state)
    async with app.run_test() as pilot:
        sidebar = app.query_one("#sidebar", SidebarWidget)
        sidebar.refresh_sessions(active_id=None)
        await pilot.pause()
        rendered = _sidebar_text(app)
        assert "a" in rendered and "b" in rendered
        # No profile → the profile line is hidden.
        assert app.query_one("#sidebar-profile").display is False


@pytest.mark.asyncio
async def test_turn_status_singular_turn():
    state = AppState()
    sess = SessionState(session_id="s", model="m")
    apply_user_prompt(sess, "hi")
    apply(sess, {"type": "session.result", "session_id": "s", "seq": 2, "stop_reason": "end_turn"})
    state.sessions["s"] = sess
    state.active_session_id = "s"
    app = _TurnStatusOnlyApp(state)
    async with app.run_test() as pilot:
        bar = app.query_one("#turn-status", TurnStatusBar)
        bar.update_status()
        await pilot.pause()
        right = str(bar.query_one("#turn-status-right").render())
        assert "1 turn" in right
        assert "1 turns" not in right


@pytest.mark.asyncio
async def test_footer_agent_availability_is_not_a_version():
    # hello_ack's agents map carries availability strings, not versions —
    # "claude-agent-acp available", never "claude-agent-acp vavailable" (#25).
    state = AppState()
    state.daemon.agents = {"claude-agent-acp": "available", "codex-acp": "1.2.3"}
    state.connection_status = "connected"
    app = _FooterOnlyApp(state)
    async with app.run_test() as pilot:
        footer = app.query_one("#footer", FooterStatusWidget)
        footer.update_status()
        await pilot.pause()
        from textual.widgets import Static

        info = str(footer.query_one("#footer-info", Static).render())
        assert "claude-agent-acp available" in info
        assert "vavailable" not in info
        assert "codex-acp v1.2.3" in info


@pytest.mark.asyncio
async def test_chat_pane_renders_whitespace_only_tool_result():
    # A command emitting only "\n" produces a whitespace-only result_text;
    # the preview extraction must not IndexError on the empty line list (#17).
    app = _ChatOnlyApp()
    async with app.run_test() as pilot:
        chat = app.query_one("#chat", ChatPaneWidget)
        sess = SessionState(session_id="s1")
        apply_user_prompt(sess, "run it")
        for frame in (
            {
                "type": "session.update",
                "session_id": "s1",
                "seq": 2,
                "update": {
                    "sessionUpdate": "tool_call",
                    "toolCallId": "t1",
                    "title": "Bash",
                    "status": "in_progress",
                },
            },
            {
                "type": "session.update",
                "session_id": "s1",
                "seq": 3,
                "update": {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": "t1",
                    "status": "completed",
                    "content": [{"type": "content", "content": {"type": "text", "text": "\n"}}],
                },
            },
        ):
            apply(sess, frame)
        chat.show_session(sess)
        await pilot.pause()
        # Renders without exception; one turn block mounted.
        assert len(list(chat.query(_TurnBlock))) == 1


@pytest.mark.asyncio
async def test_sidebar_renders_markup_hostile_titles():
    # Titles derive from the user's first prompt — "[/]" in a prompt crashed
    # the app with MarkupError before titles were escaped (#16, reproduced).
    state = AppState()
    sess = SessionState(session_id="s1", cwd="/p")
    sess.title = "fix the [/] broken [bold]tag"
    state.sessions["s1"] = sess
    app = _SidebarOnlyApp(state)
    async with app.run_test() as pilot:
        app.query_one("#sidebar", SidebarWidget).refresh_sessions()
        await pilot.pause()  # crash would surface here


def test_chat_pane_escape_preserves_backslashes_and_neutralizes_markup():
    from blemees_tui.widgets.chat_pane import _escape

    # The old hand-rolled escaper collapsed double-backslashes (verified
    # corruption) and could be bypassed for markup injection (#16).
    assert "\\\\" in _escape("C:\\\\path")
    from rich.text import Text

    rendered = Text.from_markup(_escape("[bold]x[/bold] [red]y[/]"))
    assert rendered.plain == "[bold]x[/bold] [red]y[/]"
    assert not rendered.spans  # no styling leaked through


@pytest.mark.asyncio
async def test_event_log_formatting_survives_hostile_text(isolated_state_dir, monkeypatch):
    from rich.text import Text

    from blemees_tui.state import EventLogEntry, EventLogSource
    from blemees_tui.widgets.event_log import _format_entry

    entry = EventLogEntry(
        ts_ms=0,
        source=EventLogSource.DAEMON_ERROR,
        category="bad[category]",
        message="bad [/] markup [reverse]attack",
        session_id="s1",
    )
    # Renders as literal text, no MarkupError, no styling injection — for
    # the message AND the category/sid fields.
    rendered = Text.from_markup(_format_entry(entry))
    assert "bad [/] markup" in rendered.plain
    assert "bad[category]" in rendered.plain


@pytest.mark.asyncio
async def test_debug_pane_survives_hostile_frame_reprs():
    from collections import deque

    from blemees_tui.widgets.debug_pane import DebugPane

    frames = deque([("in", {"type": "session.update", "text": "bad [/] markup [bold]x"})])
    app = _ChatOnlyApp()
    async with app.run_test() as pilot:
        app.push_screen(DebugPane(frames))
        await pilot.pause()  # MarkupError would crash here


@pytest.mark.asyncio
async def test_error_bubble_renders_after_session_switch():
    # Switching sessions detaches the error/replay/permission widgets via
    # remove_children(), but the references survived — _sync_* then updated
    # unmounted widgets and the new session's error bubble never rendered (#18).
    app = _ChatOnlyApp()
    async with app.run_test() as pilot:
        chat = app.query_one("#chat", ChatPaneWidget)
        a = SessionState(session_id="a")
        b = SessionState(session_id="b")
        # Session A shows an error bubble (mounts _errors_widget).
        apply(
            a,
            {
                "type": "session.error",
                "session_id": "a",
                "seq": 1,
                "code": "x",
                "message": "boom-a",
            },
        )
        chat.show_session(a)
        await pilot.pause()
        from textual.widgets import Static

        assert any("boom-a" in str(w.render()) for w in chat.query(Static))
        # Switch to B, which also has an error — the bubble must render in
        # the freshly mounted view, not vanish into the orphaned widget.
        apply(
            b,
            {
                "type": "session.error",
                "session_id": "b",
                "seq": 1,
                "code": "x",
                "message": "boom-b",
            },
        )
        chat.show_session(b)
        await pilot.pause()
        rendered = [str(w.render()) for w in chat.query(Static)]
        assert any("boom-b" in r for r in rendered), rendered
        assert not any("boom-a" in r for r in rendered)
