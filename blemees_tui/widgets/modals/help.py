"""Help overlay (spec §11)."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

_KEY_TABLE = """\
[b]Keys[/b]
  Ctrl+N            New session
  Ctrl+T            Attach to existing (watch)
  Ctrl+W            Close current session
  Ctrl+Shift+W      Delete current session (confirm)
  Ctrl+C            Interrupt turn (twice = quit)
  Ctrl+R            Force reconnect
  Ctrl+E            Event log overlay
  Ctrl+D            Debug pane (raw frames)
  Ctrl+S            Save transcript
  F1..F12           Switch to session N (1-12)
  Ctrl+Tab          Next session (cycles past 12)
  t                 Toggle thinking visibility
  m                 Toggle broadcast mark on active session
  Tab               Activate the composer (Esc to deactivate)
  Ctrl+J            Insert newline in composer (multiline submit)
  :                 Focus composer + open command line
  PgUp / PgDn       Scroll chat pane (PgUp pauses tailing)
  Ctrl+↑ / Ctrl+↓   Line scroll
  Home / End        Top / bottom of chat (End resumes tailing)
  ?                 This help
  q                 Quit

[b]Broadcast send[/b]
  Mark sessions with [b]m[/] (or [b]:mark[/], [b]:mark all[/]).
  Type [b]>> message[/] in the composer to fan it out to all marked
  sessions. Marked rows show ◆ in the sidebar.

[b]TUI commands (vim-style ``:``)[/b]
  :new :close :delete :interrupt :rename :cwd :model :watch :select
  :mark :help :q
  ``:select N`` jumps to session N (any number — for past F12).
  Multi-target syntax — every session-specific command takes a leading
  list of 1-indexed session numbers (empty = active session):
    ``:close 1 3 5`` close sessions 1, 3, 5
    ``:rename 1 3 my title`` rename sessions 1 & 3 to "my title"
    ``:cwd 2 /path`` relabel session 2's cwd
  ``:mark all`` marks every owned session; ``:mark clear`` clears all.
  These are intercepted by the TUI.

[b]Backend slash commands (``/``)[/b]
  Anything starting with ``/`` (e.g. /agents, /skills, /your-skill) is
  forwarded to the active backend, so Claude Code skills and Codex
  slash commands work as you'd expect.
"""


class HelpModal(ModalScreen):
    BINDINGS = [("escape", "dismiss", "Close"), ("question_mark", "dismiss", "Close")]

    DEFAULT_CSS = """
    HelpModal { align: center middle; }
    HelpModal #help-box { width: 64; padding: 1 2; border: round $accent; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="help-box"):
            yield Static(_KEY_TABLE)

    def action_dismiss(self) -> None:
        self.app.pop_screen()
