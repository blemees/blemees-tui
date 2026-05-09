"""Turn status bar — pinned above the composer.

Left side shows the active session's model name when idle, swapped for a
live spinner + elapsed-time + token estimate while a turn is in flight.
Right side carries the turn count and context-window meter. Pinning both
above the composer keeps them visible regardless of chat-pane scroll
position.

The post-turn summary that used to occupy the left side is intentionally
gone — the chat pane's own per-turn divider already plays that role.
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


def _format_tokens(n: int) -> str:
    """Compact human form for token counts: 1234 → ``1.2k``, 1_234_567 → ``1.2M``."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M tok"
    if n >= 1_000:
        return f"{n // 1_000}k tok"
    return f"{n} tok"


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
        color: $text-muted;
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
        turn_count = f"{len(active.turns)} turns" if active else "0 turns"
        right_bits: list[str] = [turn_count]
        if active is not None and active.context_window:
            pct = round(active.context_tokens / active.context_window * 100)
            if pct > 100:
                # The agent's window inference is wrong (e.g. a 1M-beta
                # session whose model id lacks the ``[1m]`` marker, so the
                # daemon defaulted to 200k). Showing "600%" misleads more
                # than it informs — fall back to the absolute count and
                # flag it so the user can spot the misconfiguration.
                right_bits.append(f"[$error]ctx {_format_tokens(active.context_tokens)}[/]")
            else:
                colour = "$success" if pct < 50 else "$warning" if pct < 80 else "$error"
                right_bits.append(f"[{colour}]ctx {pct}%[/]")
        right = " · ".join(right_bits)

        in_flight = self._in_flight_turn(active)
        if in_flight is not None and active is not None:
            tracking = (active.session_id, len(active.turns) - 1)
            if self._tracked != tracking:
                self._started_monotonic = _now()
                self._tracked = tracking
            elapsed = max(0.0, _now() - (self._started_monotonic or _now()))
            spinner = _SPINNER[int(_now() * 10) % len(_SPINNER)]
            tokens = self._approx_tokens(in_flight)
            # $warning hue marks the live state; the model-name idle state
            # picks up the bar's default $text-muted colour.
            left = f"[$warning]{spinner} {elapsed:.1f}s · ~{tokens} tok[/]"
        else:
            self._tracked = None
            self._started_monotonic = None
            left = active.model if (active is not None and active.model) else ""

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
