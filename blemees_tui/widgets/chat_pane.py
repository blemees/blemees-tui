"""Chat transcript pane (spec §9).

Incremental render: each ``Turn`` gets its own ``Static`` block (Markdown
content), and ``show_session()`` only mounts new turns / patches the
in-progress turn rather than rebuilding the whole pane on every frame.
This keeps streaming-delta cost flat in the number of turns.

Watch-mode (§8.3) renders a banner above the transcript and replaces the
composer surface with a button row. The composer itself is disabled by
the app — chat_pane only needs to expose the banner + buttons.
"""

from __future__ import annotations

import difflib
import time
from datetime import UTC, datetime

from rich.console import Group, RenderableType
from rich.syntax import Syntax
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Button, Markdown, Static

from ..state import (
    SessionMode,
    SessionState,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
    Turn,
)


class _TurnBlock(Vertical):
    """Single-turn render slot.

    Maintains stable widgets per content block so streaming
    ``agent.delta`` frames update text in place via ``Markdown.update()``
    rather than remounting (which both flickers and starves Textual's
    Markdown widget of the chance to actually render).

    While the turn is in flight the bottom row shows a live progress
    indicator — wall-clock seconds + token count, ticking every 250ms —
    so the user can see the agent is doing work even before the first
    token streams in.
    """

    DEFAULT_CSS = """
    _TurnBlock {
        height: auto;
        width: 100%;
        padding: 0 0 1 0;
    }
    _TurnBlock > * { width: 100%; }
    _TurnBlock Markdown { margin: 0; padding: 0; background: transparent; }
    _TurnBlock Static { height: auto; }
    _TurnBlock Static.user-line {
        background: $surface-lighten-2;
        padding: 0 1;
        margin-bottom: 1;
    }
    _TurnBlock Static.tool-block { margin-bottom: 1; }
    _TurnBlock #progress { color: $warning; }
    _TurnBlock #divider { color: $text-muted; }
    """

    def __init__(self) -> None:
        super().__init__()
        self._user: Static | None = None
        # Parallel arrays: tag tells what kind is currently mounted at idx,
        # so we can swap if a tool_use slips in between deltas.
        self._block_widgets: list[Widget] = []
        self._block_tags: list[str] = []
        self._progress: Static | None = None
        self._divider: Static | None = None
        self._started_monotonic: float | None = None
        self._tick_handle = None  # set_interval handle while turn is live

    # ------------------------------------------------------------------
    # Public render entry point
    # ------------------------------------------------------------------

    def render_turn(self, turn: Turn, *, show_thinking: bool = False) -> None:
        # 1. User line — mount once, never recreate.
        if turn.user_text:
            label = f"[b]❯[/] {_escape(turn.user_text)}"
            if self._user is None:
                self._user = Static(label, classes="user-line")
                self.mount(self._user)
            else:
                self._user.update(label)

        # 2. Per-block widgets, indexed by position in turn.blocks.
        for idx, block in enumerate(turn.blocks):
            tag = _block_tag(block)
            if idx < len(self._block_widgets):
                if self._block_tags[idx] != tag:
                    self._block_widgets[idx].remove()
                    new_widget = self._mount_block(block, show_thinking)
                    self._block_widgets[idx] = new_widget
                    self._block_tags[idx] = tag
                else:
                    self._update_block(self._block_widgets[idx], block, show_thinking)
            else:
                self._block_widgets.append(self._mount_block(block, show_thinking))
                self._block_tags.append(tag)

        # 3. Progress row + ticker (only while in flight).
        self._sync_progress(turn)

        # 4. Result divider — appears once the turn locks.
        if turn.duration_ms is not None:
            u = turn.usage
            bits = [
                f"{turn.duration_ms / 1000:.1f}s",
                f"↑{u.input_tokens}",
                f"↓{u.output_tokens}",
            ]
            if u.reasoning_output_tokens:
                bits.append(f"🧠 {u.reasoning_output_tokens}")
            if turn.result_subtype and turn.result_subtype != "success":
                bits.append(f"[red]{turn.result_subtype}[/]")
            divider = f"─── {' · '.join(bits)} ───"
            if self._divider is None:
                self._divider = Static(divider, id="divider")
                self.mount(self._divider)
            else:
                self._divider.update(divider)

    # ------------------------------------------------------------------
    # Per-block widget mount/update
    # ------------------------------------------------------------------

    def _mount_block(self, block, show_thinking: bool) -> Widget:
        # Anchor: keep the progress / result divider pinned to the bottom
        # of the turn by mounting new content *before* whichever footer
        # widget is currently present. Without this, streaming blocks slot
        # in after the progress ticker and the timing/token row buries
        # itself in the middle of the response.
        before = self._progress or self._divider
        if isinstance(block, TextBlock):
            md = Markdown(block.text or " ")
            self.mount(md, before=before)
            return md
        if isinstance(block, ThinkingBlock):
            w = Static(_format_thinking(block, show_thinking))
            self.mount(w, before=before)
            return w
        if isinstance(block, ToolUseBlock):
            w = Static(_format_tool(block), classes="tool-block")
            self.mount(w, before=before)
            return w
        w = Static("")
        self.mount(w, before=before)
        return w

    def _update_block(self, widget: Widget, block, show_thinking: bool) -> None:
        if isinstance(block, TextBlock) and isinstance(widget, Markdown):
            # Markdown.update is async — the returned coroutine is fire-and-
            # forget; Textual schedules the re-render on the next tick.
            widget.update(block.text or " ")
        elif isinstance(block, ThinkingBlock) and isinstance(widget, Static):
            widget.update(_format_thinking(block, show_thinking))
        elif isinstance(block, ToolUseBlock) and isinstance(widget, Static):
            widget.update(_format_tool(block))

    # ------------------------------------------------------------------
    # Progress row + ticker
    # ------------------------------------------------------------------

    def _sync_progress(self, turn: Turn) -> None:
        if turn.locked:
            self._stop_tick()
            if self._progress is not None:
                self._progress.remove()
                self._progress = None
            return

        if self._started_monotonic is None:
            self._started_monotonic = _now()

        if self._progress is None:
            self._progress = Static(self._progress_text(turn), id="progress")
            self.mount(self._progress)
        else:
            self._progress.update(self._progress_text(turn))

        if self._tick_handle is None:
            # 250ms — fast enough to feel live, slow enough to be cheap.
            self._tick_handle = self.set_interval(0.25, lambda: self._on_tick(turn))

    def _on_tick(self, turn: Turn) -> None:
        if turn.locked or self._progress is None:
            self._stop_tick()
            return
        self._progress.update(self._progress_text(turn))

    def _stop_tick(self) -> None:
        if self._tick_handle is not None:
            try:
                self._tick_handle.stop()
            except Exception:
                pass
            self._tick_handle = None

    def _progress_text(self, turn: Turn) -> str:
        spinner = _SPINNER[int(_now() * 10) % len(_SPINNER)]
        elapsed = 0.0
        if self._started_monotonic is not None:
            elapsed = max(0.0, _now() - self._started_monotonic)
        # Token estimate: sum lengths of streamed text + tool inputs + tool
        # outputs, divided by 4 (rough chars-per-token ratio). Tool blocks
        # are counted because Claude Code suppresses text deltas by default
        # — without including them, the live count sits at 0 for most of
        # the turn even though plenty of work is happening. Once
        # ``agent.result`` arrives we swap to the canonical usage on the
        # divider.
        chars = 0
        for b in turn.blocks:
            if isinstance(b, TextBlock):
                chars += len(b.text)
            elif isinstance(b, ThinkingBlock):
                chars += len(b.text)
            elif isinstance(b, ToolUseBlock):
                if b.input is not None:
                    chars += len(repr(b.input))
                if b.result_text:
                    chars += len(b.result_text)
        approx_tokens = chars // 4
        return f"{spinner} {elapsed:.1f}s · ~{approx_tokens} tok"

    # Reset internal state when the widget is re-attached to a new turn —
    # not currently used (one block per turn) but keeps things tidy if the
    # caller ever resets.
    def reset(self) -> None:
        self._stop_tick()
        self._started_monotonic = None
        self._block_widgets = []
        self._block_tags = []
        self._progress = None
        self._divider = None


class ChatPaneWidget(Widget):
    DEFAULT_CSS = """
    ChatPaneWidget {
        width: 1fr;
        height: 1fr;
    }
    ChatPaneWidget #chat-scroll {
        width: 100%;
        height: 1fr;
        padding: 1 2;
        align: left bottom;
    }
    ChatPaneWidget #chat-scroll > * { width: 100%; }
    ChatPaneWidget #watch-banner { dock: top; height: auto; width: 100%; padding: 0 2; }
    ChatPaneWidget #watch-buttons { dock: bottom; height: 3; width: 100%; padding: 0 2; }
    ChatPaneWidget #watch-buttons Button { margin-right: 2; }
    """

    class TakeOwnership(Message):
        def __init__(self, session_id: str) -> None:
            super().__init__()
            self.session_id = session_id

    class StopWatching(Message):
        def __init__(self, session_id: str) -> None:
            super().__init__()
            self.session_id = session_id

    class Reclaim(Message):
        def __init__(self, session_id: str) -> None:
            super().__init__()
            self.session_id = session_id

    class MoveToHistory(Message):
        def __init__(self, session_id: str) -> None:
            super().__init__()
            self.session_id = session_id

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._session_id: str | None = None
        self._turn_widgets: list[_TurnBlock] = []
        self._show_thinking: bool = False
        self._empty_state: Static | None = None
        self._banner: Static | None = None
        self._buttons: Horizontal | None = None
        self._banner_state: str = ""  # "watching" | "detached" | "closed" | ""
        self._errors_widget: Static | None = None
        self._gap_widget: Static | None = None
        self._replay_widget: Static | None = None

    def compose(self) -> ComposeResult:
        scroll = VerticalScroll(id="chat-scroll")
        # VerticalScroll is focusable by default so it can take keyboard
        # scroll input — but we drive scrolling via app-level priority
        # bindings, so removing it from the focus cycle keeps Tab landing
        # on the composer in a single press.
        scroll.can_focus = False
        yield scroll

    def set_show_thinking(self, value: bool) -> None:
        if value == self._show_thinking:
            return
        self._show_thinking = value
        # Force repaint of all turns so collapsed/expanded state is current.
        self._turn_widgets.clear()
        sess_widget = self._session
        if sess_widget is not None:
            self.show_session(sess_widget, force=True)

    @property
    def _session(self) -> SessionState | None:
        return getattr(self, "_session_ref", None)

    def show_session(self, session: SessionState | None, *, force: bool = False) -> None:
        scroll = self.query_one("#chat-scroll", VerticalScroll)

        # Switching sessions → reset bookkeeping and remount.
        if force or session is None or session.session_id != self._session_id:
            scroll.remove_children()
            self._turn_widgets = []
            self._empty_state = None
            self._session_id = session.session_id if session is not None else None

        self._session_ref = session

        if session is None:
            if self._empty_state is None:
                self._empty_state = Static(
                    "[dim]No session selected — press Ctrl+N to create one.[/]"
                )
                scroll.mount(self._empty_state)
            return

        # Mount widgets for any new turns.
        while len(self._turn_widgets) < len(session.turns):
            block = _TurnBlock()
            self._turn_widgets.append(block)
            scroll.mount(block)

        # Repaint every unlocked turn (the in-progress one + any that
        # already streamed but had a delta land late). Locked turns are
        # touched once, the first time we see them done.
        for idx, (turn, widget) in enumerate(zip(session.turns, self._turn_widgets, strict=False)):
            if not turn.locked or idx == len(session.turns) - 1:
                widget.render_turn(turn, show_thinking=self._show_thinking)
            elif not getattr(widget, "_locked_painted", False):
                widget.render_turn(turn, show_thinking=self._show_thinking)
                widget._locked_painted = True  # type: ignore[attr-defined]

        # Replay-gap banner, if any (§9.8).
        self._sync_replay_gap(session, scroll)

        # Replay-progress overlay (loading older transcript on attach).
        self._sync_replay_progress(session, scroll)

        # Inline error bubbles for session-scoped errors (§9.8).
        self._sync_errors(session, scroll)

        # Auto-scroll the latest turn into view. Mounts are async — the
        # widgets have height 0 until the next layout pass, so scrolling
        # right now would clamp to ~0 and stay at the top. Defer until
        # after the next refresh.
        if self._turn_widgets:
            self.call_after_refresh(scroll.scroll_end, animate=False)

        # Banner / button row for non-owned modes.
        self._sync_banner(session)

    # ------------------------------------------------------------------
    # External scroll API (used by app-level keybindings)
    # ------------------------------------------------------------------

    def scroll_up_page(self) -> None:
        self.query_one("#chat-scroll", VerticalScroll).scroll_page_up(animate=False)

    def scroll_down_page(self) -> None:
        self.query_one("#chat-scroll", VerticalScroll).scroll_page_down(animate=False)

    def scroll_up_line(self) -> None:
        self.query_one("#chat-scroll", VerticalScroll).scroll_relative(y=-1, animate=False)

    def scroll_down_line(self) -> None:
        self.query_one("#chat-scroll", VerticalScroll).scroll_relative(y=1, animate=False)

    def scroll_to_top(self) -> None:
        self.query_one("#chat-scroll", VerticalScroll).scroll_home(animate=False)

    def scroll_to_bottom(self) -> None:
        self.query_one("#chat-scroll", VerticalScroll).scroll_end(animate=False)

    def _sync_replay_gap(self, session: SessionState, scroll: VerticalScroll) -> None:
        if not session.replay_gap:
            if self._gap_widget is not None:
                self._gap_widget.remove()
                self._gap_widget = None
            return
        text = (
            "[reverse yellow] ⚠ Replay gap [/]  this session's transcript may be incomplete — "
            "events were dropped from the daemon ring before the watcher attached."
        )
        if self._gap_widget is None:
            self._gap_widget = Static(text)
            scroll.mount(self._gap_widget, before=self._turn_widgets[0] if self._turn_widgets else None)
        else:
            self._gap_widget.update(text)

    def _sync_replay_progress(
        self, session: SessionState, scroll: VerticalScroll
    ) -> None:
        target = session.replay_target_seq
        if not target or session.last_seen_seq >= target:
            if self._replay_widget is not None:
                self._replay_widget.remove()
                self._replay_widget = None
            return
        start = session.replay_start_seq
        # Total span of frames to consume during this replay window.
        total = max(1, target - start)
        seen = max(0, session.last_seen_seq - start)
        pct = min(100, int(100 * seen / total))
        text = (
            f"[reverse cyan] ⟳ Loading transcript [/]  "
            f"{seen:,} / {total:,} frames ({pct}%)"
        )
        if self._replay_widget is None:
            self._replay_widget = Static(text)
            scroll.mount(
                self._replay_widget,
                before=self._turn_widgets[0] if self._turn_widgets else None,
            )
        else:
            self._replay_widget.update(text)

    def _sync_errors(self, session: SessionState, scroll: VerticalScroll) -> None:
        errs = session.pending_errors
        if not errs:
            if self._errors_widget is not None:
                self._errors_widget.remove()
                self._errors_widget = None
            return
        # Render the most recent few; older ones are still in the event log.
        rendered = "\n\n".join(_render_error(e) for e in errs[-5:])
        if self._errors_widget is None:
            self._errors_widget = Static(rendered)
            scroll.mount(self._errors_widget)
        else:
            self._errors_widget.update(rendered)

    # ------------------------------------------------------------------
    # Watch / detached / closed banner
    # ------------------------------------------------------------------

    def _sync_banner(self, session: SessionState) -> None:
        target = ""
        if session.mode == SessionMode.WATCHING:
            target = "watching"
        elif session.mode == SessionMode.DETACHED:
            target = "detached"
        elif session.mode == SessionMode.CLOSED:
            target = "closed"

        if target == self._banner_state:
            if target and self._banner is not None:
                self._banner.update(_banner_text(session, target))
            return

        # Tear down any prior banner / buttons before rebuilding.
        for existing_id in ("#watch-banner", "#watch-buttons"):
            for w in list(self.query(existing_id)):
                w.remove()
        self._banner = None
        self._buttons = None

        self._banner_state = target
        if not target:
            return

        self._banner = Static(_banner_text(session, target), id="watch-banner")
        self.mount(self._banner)
        self._buttons = Horizontal(*_banner_buttons(target), id="watch-buttons")
        self.mount(self._buttons)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        sid = self._session_id
        if not sid:
            return
        bid = event.button.id or ""
        if bid == "btn-take-ownership":
            self.post_message(self.TakeOwnership(sid))
        elif bid == "btn-stop-watching":
            self.post_message(self.StopWatching(sid))
        elif bid == "btn-reclaim":
            self.post_message(self.Reclaim(sid))
        elif bid == "btn-history":
            self.post_message(self.MoveToHistory(sid))


# ---------------------------------------------------------------------------
# Render helpers
# ---------------------------------------------------------------------------


_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def _now() -> float:
    return time.monotonic()


def _block_tag(block) -> str:
    if isinstance(block, TextBlock):
        return "text"
    if isinstance(block, ThinkingBlock):
        return "thinking"
    if isinstance(block, ToolUseBlock):
        return "tool"
    return "?"


def _format_thinking(block: ThinkingBlock, show_thinking: bool) -> str:
    if show_thinking:
        return f"[dim italic]🧠 {_escape(block.text)}[/]"
    return (
        f"[dim]🧠 reasoning · {len(block.text)} chars (press [b]t[/b] to expand)[/]"
    )


def _format_tool(block: ToolUseBlock) -> RenderableType:
    name = block.name or "?"
    edit_renderable = _format_edit_diff(name, block.input, block)
    if edit_renderable is not None:
        return edit_renderable
    write_renderable = _format_write_content(name, block.input, block)
    if write_renderable is not None:
        return write_renderable

    head = f"▸ {_escape(name)}({_escape(_format_tool_input(block.input))})"
    if block.is_error:
        head = f"[red]{head}[/]"
    if block.result_text:
        preview = block.result_text.strip().splitlines()[0][:120]
        colour = "red" if block.is_error else "dim"
        head += f"\n  [{colour}]→ {_escape(preview)}[/]"
    return head


def _format_tool_input(value) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return ", ".join(f"{k}={v!r}" for k, v in value.items())
    if isinstance(value, list):
        # Codex shell argv: render as a single shell-style string.
        return " ".join(str(v) for v in value)
    if isinstance(value, str):
        return value
    return repr(value)


def _edit_path(value) -> str:
    if isinstance(value, dict):
        return str(value.get("file_path", "") or "")
    return ""


# Tool names whose input shape is ``{file_path, old_string, new_string}``
# and is best viewed as a unified diff rather than a key=value blob.
_DIFF_TOOL_NAMES = frozenset({"Edit", "edit"})


def _format_edit_diff(
    name: str, value, block: ToolUseBlock
) -> RenderableType | None:
    """Render Claude's Edit-tool input as a syntax-highlighted unified diff
    via Pygments (same machinery as our Markdown code blocks). Returns None
    when this tool/shape doesn't have a diff to show, so the caller can
    fall through to the generic string renderer.
    """
    if name not in _DIFF_TOOL_NAMES or not isinstance(value, dict):
        return None
    old = value.get("old_string")
    new = value.get("new_string")
    if not isinstance(old, str) or not isinstance(new, str):
        return None
    raw_diff = list(
        difflib.unified_diff(
            old.splitlines(),
            new.splitlines(),
            lineterm="",
            n=3,
        )
    )
    if not raw_diff:
        return None
    # Drop difflib's `--- ` / `+++ ` file headers — we render the path on
    # the head line so the lexer doesn't waste two rows on placeholders.
    diff_text = "\n".join(
        line for line in raw_diff if not line.startswith(("--- ", "+++ "))
    )
    head = Text(f"▸ {name}({_edit_path(value)})")
    syntax = Syntax(
        diff_text,
        "diff",
        theme="monokai",
        background_color="default",
        word_wrap=False,
    )
    parts: list[RenderableType] = [head, syntax]
    if block.result_text:
        preview = block.result_text.strip().splitlines()[0][:120]
        style = "red" if block.is_error else "dim"
        parts.append(Text(f"  → {preview}", style=style))
    return Group(*parts)


# Tools whose input is ``{file_path, content}`` and renders best as a
# syntax-highlighted code block.
_WRITE_TOOL_NAMES = frozenset({"Write", "write"})


def _format_write_content(
    name: str, value, block: ToolUseBlock
) -> RenderableType | None:
    """Render Claude's Write-tool input as a syntax-highlighted code block.
    The lexer is guessed from the file extension so e.g. ``.py`` files use
    the Python lexer."""
    if name not in _WRITE_TOOL_NAMES or not isinstance(value, dict):
        return None
    content = value.get("content")
    file_path = value.get("file_path", "") or ""
    if not isinstance(content, str):
        return None
    head = Text(f"▸ {name}({file_path})")
    syntax = Syntax(
        content,
        _lexer_for_path(file_path) or "text",
        theme="monokai",
        background_color="default",
        line_numbers=False,
        word_wrap=False,
    )
    parts: list[RenderableType] = [head, syntax]
    if block.result_text:
        preview = block.result_text.strip().splitlines()[0][:120]
        style = "red" if block.is_error else "dim"
        parts.append(Text(f"  → {preview}", style=style))
    return Group(*parts)


def _lexer_for_path(path: str) -> str | None:
    """Map a file extension to a Pygments short-name. Falls back to None
    when the extension isn't recognised; the caller substitutes ``text``."""
    if not path or "." not in path:
        return None
    ext = path.rsplit(".", 1)[-1].lower()
    return {
        "py": "python",
        "pyi": "python",
        "js": "javascript",
        "mjs": "javascript",
        "cjs": "javascript",
        "ts": "typescript",
        "tsx": "tsx",
        "jsx": "jsx",
        "json": "json",
        "yaml": "yaml",
        "yml": "yaml",
        "toml": "toml",
        "md": "markdown",
        "markdown": "markdown",
        "html": "html",
        "css": "css",
        "scss": "scss",
        "sh": "bash",
        "bash": "bash",
        "zsh": "bash",
        "rs": "rust",
        "go": "go",
        "java": "java",
        "kt": "kotlin",
        "swift": "swift",
        "rb": "ruby",
        "php": "php",
        "c": "c",
        "h": "c",
        "cpp": "cpp",
        "cc": "cpp",
        "hpp": "cpp",
        "cs": "csharp",
        "sql": "sql",
        "xml": "xml",
        "dockerfile": "docker",
    }.get(ext)


def _escape(text: str) -> str:
    """Escape Rich markup characters to avoid the user injecting tags."""
    return text.replace("[", r"\[").replace("\\\\", "\\")


def _banner_text(session: SessionState, mode: str) -> str:
    if mode == "watching":
        owner = session.owner_pid if session.owner_pid is not None else "?"
        started = _fmt_clock_ms(session.started_at_ms)
        return f"[reverse] 👀 Watching [/]  owner pid {owner} · started {started}"
    if mode == "detached":
        pid = session.taken_by_pid if session.taken_by_pid is not None else "?"
        when = _fmt_clock_ms(session.last_active_at_ms)
        return f"[reverse yellow] ⊘ Detached [/]  taken over by pid {pid} at {when}"
    if mode == "closed":
        when = _fmt_clock_ms(session.last_active_at_ms)
        reason = session.closed_reason or "owner_closed"
        return f"[reverse red] ✓ Closed [/]  by {reason} at {when}"
    return ""


def _banner_buttons(mode: str) -> list[Button]:
    if mode == "watching":
        return [
            Button("Take ownership", id="btn-take-ownership", variant="primary"),
            Button("Stop watching", id="btn-stop-watching"),
        ]
    if mode == "detached":
        return [
            Button("Reclaim", id="btn-reclaim", variant="primary"),
            Button("Move to history", id="btn-history"),
        ]
    if mode == "closed":
        return [Button("Move to history", id="btn-history")]
    return []


def _render_error(err: dict) -> str:
    code = str(err.get("code", "error"))
    msg = str(err.get("message", "") or err.get("error", {}).get("message", ""))
    hint = _hint_for(code)
    body = f"[reverse red] ⚠ {code} [/]  {_escape(msg)}"
    if hint:
        body += f"\n[dim]→ {hint}[/]"
    return body


def _hint_for(code: str) -> str:
    return {
        "auth_failed": "run `claude /login` or `codex login` in a terminal, then retry.",
        "backend_crashed": "the backend process exited; press [Re-open] or send a new turn.",
        "spawn_failed": "the daemon couldn't spawn the backend — check $PATH.",
        "session_busy": "this session is mid-turn; wait for the result.",
        "session_unknown": "the daemon doesn't recognise this session anymore.",
    }.get(code, "")


def _fmt_clock_ms(ms: int) -> str:
    if not ms:
        return "—"
    return datetime.fromtimestamp(ms / 1000, tz=UTC).astimezone().strftime("%H:%M:%S")
