"""Turn status bar — pinned above the composer.

Surfaces two read-only counters that used to live elsewhere: the live
elapsed-time + token estimate that streamed inside ``_TurnBlock`` while a
turn was in flight (left), and the total turn count that lived in the
footer chip (right). Pinning both above the composer keeps them visible
regardless of chat-pane scroll position.

Once the turn locks (``agent.result`` lands), the running spinner is
swapped for the same wall-clock + ``↑in / ↓out`` token summary that
appears in the chat pane's per-turn divider — the user keeps seeing the
final stats for the most recent turn until a new one kicks off.
"""

from __future__ import annotations

import time

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widget import Widget
from textual.widgets import Static

from ..state import AppState, TextBlock, ThinkingBlock, ToolUseBlock, Turn

_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def _now() -> float:
    return time.monotonic()


def _format_locked_turn(turn: Turn) -> str:
    """Same shape as the chat-pane result divider: duration, ↑input,
    ↓output, optional 🧠 reasoning, optional non-success subtype."""
    u = turn.usage
    bits = [
        f"{(turn.duration_ms or 0) / 1000:.1f}s",
        f"↑{u.input_tokens}",
        f"↓{u.output_tokens}",
    ]
    if u.reasoning_output_tokens:
        bits.append(f"🧠 {u.reasoning_output_tokens}")
    if turn.result_subtype and turn.result_subtype != "success":
        bits.append(f"[red]{turn.result_subtype}[/]")
    return " · ".join(bits)


class TurnStatusBar(Widget):
    DEFAULT_CSS = """
    TurnStatusBar {
        height: 1;
        width: 100%;
        padding: 0 1;
    }
    TurnStatusBar > Horizontal { height: 1; width: 100%; }
    TurnStatusBar Static { height: 1; }
    TurnStatusBar #turn-status-left {
        width: 1fr;
        content-align: left middle;
        color: $warning;
    }
    TurnStatusBar #turn-status-right {
        width: auto;
        content-align: right middle;
        color: $text-muted;
    }
    """

    def __init__(self, state: AppState, **kwargs) -> None:
        super().__init__(**kwargs)
        self._state = state
        self._tick_handle = None
        self._started_monotonic: float | None = None
        # Identity of the in-flight turn we're timing —
        # ``(session_id, turn_index)``. Lets the bar reset its monotonic
        # anchor when the user switches sessions or a new turn begins.
        self._tracked: tuple[str, int] | None = None

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield Static("", id="turn-status-left")
            yield Static("", id="turn-status-right")

    def on_mount(self) -> None:
        # 250ms — fast enough to feel live, slow enough to be cheap. Same
        # cadence as the in-turn progress ticker we replaced.
        self._tick_handle = self.set_interval(0.25, self.update_status)
        self.update_status()

    def update_status(self) -> None:
        s = self._state
        active = s.sessions.get(s.active_session_id) if s.active_session_id else None
        right = f"{len(active.turns)} turns" if active else "0 turns"

        left = ""
        in_flight = self._in_flight_turn(active)
        if in_flight is not None and active is not None:
            tracking = (active.session_id, len(active.turns) - 1)
            if self._tracked != tracking:
                self._started_monotonic = _now()
                self._tracked = tracking
            elapsed = max(0.0, _now() - (self._started_monotonic or _now()))
            spinner = _SPINNER[int(_now() * 10) % len(_SPINNER)]
            tokens = self._approx_tokens(in_flight)
            left = f"{spinner} {elapsed:.1f}s · ~{tokens} tok"
        else:
            self._tracked = None
            self._started_monotonic = None
            last = self._last_locked_turn(active)
            if last is not None:
                # Dimmed so the bar reads as "past" rather than "live" — the
                # absent spinner already carries that signal, but the colour
                # shift makes it unambiguous against the streaming state's
                # $warning hue.
                left = f"[$text-muted]{_format_locked_turn(last)}[/]"

        try:
            self.query_one("#turn-status-left", Static).update(left)
            self.query_one("#turn-status-right", Static).update(right)
        except Exception:
            pass

    @staticmethod
    def _in_flight_turn(active) -> Turn | None:
        if active is None or not active.turn_active or not active.turns:
            return None
        last = active.turns[-1]
        return last if not last.locked else None

    @staticmethod
    def _last_locked_turn(active) -> Turn | None:
        """Most recent turn that has both ``locked`` and a known
        ``duration_ms`` — i.e. a real ``agent.result`` landed for it.
        Without a duration we have nothing to display, so skip."""
        if active is None or not active.turns:
            return None
        for turn in reversed(active.turns):
            if turn.locked and turn.duration_ms is not None:
                return turn
        return None

    @staticmethod
    def _approx_tokens(turn: Turn) -> int:
        # Mirrors the old _TurnBlock estimator: sum streamed text + tool
        # input/output lengths and divide by four. Tool blocks count
        # because Claude Code suppresses text deltas by default — without
        # them the live counter would sit at 0 for most of the turn.
        chars = 0
        for b in turn.blocks:
            if isinstance(b, (TextBlock, ThinkingBlock)):
                chars += len(b.text)
            elif isinstance(b, ToolUseBlock):
                if b.input is not None:
                    chars += len(repr(b.input))
                if b.result_text:
                    chars += len(b.result_text)
        return chars // 4
