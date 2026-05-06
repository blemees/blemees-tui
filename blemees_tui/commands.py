"""TUI command parser (spec §10, vim-style ``:`` prefix).

Pure: ``parse(text) → Command | None``. The app dispatches on the
returned dataclass; this module has no Textual / connection dependencies
so the parser stays unit-testable.

The prefix is ``:`` (vim convention) rather than ``/`` so the
``/``-prefixed namespace stays free for the active backend — Claude
Code skills and Codex slash commands are forwarded verbatim.

Supported commands:

* ``:new`` — open the new-session modal.
* ``:close [N N …]`` — close the listed sessions (1-indexed). Empty
  arg = the active session.
* ``:delete [N N …]`` — close + delete. Same arg conventions.
* ``:interrupt [N N …]`` — interrupt the listed sessions' turns.
* ``:rename [N N …] <title>`` — rename. Without leading indices, applies
  to the active session.
* ``:cwd [N N …] <path>`` — relabel cwd in the UI (does not rebind the
  backend's cwd; that needs a fresh session).
* ``:model [N N …] <name>`` — relabel the model in the UI.
* ``:watch <session-id>`` — open a watch on the given UUID.
* ``:select <N>`` — switch to session number N (1-indexed; for sessions
  past F12 where the function-key shortcuts run out).
* ``:mark [N N …]`` — toggle the broadcast mark on the listed sessions
  (or the active one if no indices are given).
* ``:mark all`` / ``:mark clear`` — batch helpers (mark every OWNED
  session / clear every mark).
* ``:help`` — open the help overlay.
* ``:q`` / ``:quit`` — quit the app.

A composer message starting with ``>> `` (note the trailing space) is
fanned out to every marked session instead of going only to the active
one.

All session-specific commands accept a leading list of 1-indexed
session numbers:

* Action commands (``:close``, ``:delete``, ``:interrupt``, ``:mark``)
  take ``[N N …]`` only.
* Value commands (``:rename``, ``:cwd``, ``:model``) take
  ``[N N …] <value>`` — leading numerics are indices, the remainder is
  the value. ``:rename hi`` applies ``hi`` to the active session;
  ``:rename 1 3 hi`` applies it to sessions 1 and 3.

Bad indices (non-numeric or out of range) are logged to the event
overlay; the rest still execute.

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
    name: str  # "new" | "close" | … (stripped of leading prefix)
    arg: str = ""  # rest of the line (single argument)
    is_unknown: bool = False
    raw: str = ""  # original input including the prefix


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
    "mark",
    "help",
    "q",
    "quit",
}


def parse(text: str) -> Command | None:
    """Return a ``Command`` if ``text`` is a TUI command, else ``None``."""
    if not text or not text.startswith(PREFIX):
        return None
    body = text[len(PREFIX) :].strip()
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
    head = prefix[len(PREFIX) :].lower()
    return [f"{PREFIX}{name}" for name in sorted(_KNOWN) if name.startswith(head)]
