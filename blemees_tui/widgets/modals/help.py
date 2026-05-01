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
  1..9              Switch to session N
  Ctrl+Tab          Next session
  t                 Toggle thinking visibility
  Tab               Activate the composer (Esc to deactivate)
  :                 Focus composer + open command line
  PgUp / PgDn       Scroll chat pane
  Ctrl+↑ / Ctrl+↓   Line scroll
  Home / End        Top / bottom of chat
  ?                 This help
  q                 Quit

[b]TUI commands (vim-style ``:``)[/b]
  :new :close :delete :interrupt :rename :cwd :model :watch :help :q
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
