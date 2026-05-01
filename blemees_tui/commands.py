"""TUI command parser (spec §10, vim-style ``:`` prefix).

Pure: ``parse(text) → Command | None``. The app dispatches on the
returned dataclass; this module has no Textual / connection dependencies
so the parser stays unit-testable.

The prefix is ``:`` (vim convention) rather than ``/`` so the
``/``-prefixed namespace stays free for the active backend — Claude
Code skills and Codex slash commands are forwarded verbatim.

Supported commands:

* ``:new`` — open the new-session modal.
* ``:close`` — close the active session (no delete).
* ``:delete`` — close + delete the active session.
* ``:interrupt`` — interrupt the active session's turn.
* ``:rename <title>`` — rename the active session.
* ``:cwd <path>`` — relabel the active session's cwd in the UI (does not
  rebind the backend's cwd; that needs a fresh session).
* ``:model <name>`` — relabel the active session's model in the UI.
* ``:watch <session-id>`` — open a watch on the given UUID.
* ``:select <N>`` — switch to session number N (1-indexed; for sessions
  past Ctrl+0/10 where the digit shortcuts run out).
* ``:help`` — open the help overlay.
* ``:q`` / ``:quit`` — quit the app.

Unknown commands return ``Command(name, raw, is_unknown=True)`` so the
app can surface a hint instead of silently swallowing the input.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

PREFIX = ":"

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


@dataclass
class Command:
    name: str            # "new" | "close" | … (stripped of leading prefix)
    arg: str = ""        # rest of the line (single argument)
    is_unknown: bool = False
    raw: str = ""        # original input including the prefix


_KNOWN = {
    "new",
    "close",
    "delete",
    "interrupt",
    "rename",
    "cwd",
    "model",
    "watch",
    "select",
    "help",
    "q",
    "quit",
}


def parse(text: str) -> Command | None:
    """Return a ``Command`` if ``text`` is a TUI command, else ``None``."""
    if not text or not text.startswith(PREFIX):
        return None
    body = text[len(PREFIX):].strip()
    if not body:
        return None
    head, _, rest = body.partition(" ")
    name = head.lower()
    arg = rest.strip()
    if name not in _KNOWN:
        return Command(name=name, arg=arg, is_unknown=True, raw=text)
    return Command(name=name, arg=arg, raw=text)


def is_uuid(s: str) -> bool:
    return bool(_UUID_RE.match(s))


def completions(prefix: str) -> list[str]:
    """Tab-complete suggestions. ``:n`` → ``[:new]`` etc."""
    if not prefix.startswith(PREFIX):
        return []
    head = prefix[len(PREFIX):].lower()
    return [f"{PREFIX}{name}" for name in sorted(_KNOWN) if name.startswith(head)]
