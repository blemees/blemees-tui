"""Active plan panel — pinned above the composer.

Surfaces the active session's ACP ``plan`` (the agent's task list, #2) as a
checklist. Updates as the agent emits ``plan`` session updates. Hidden when
the active session has no plan yet.

Detail rendering lives here rather than inline in the chat transcript so
the working list stays visible regardless of scroll position — that's
the whole point of an agent's checklist.
"""

from __future__ import annotations

from textual.widgets import Static

from ..state import AppState, SessionState


class TodoPanel(Static):
    DEFAULT_CSS = """
    TodoPanel {
        height: auto;
        max-height: 12;
        width: 100%;
        padding: 0 2;
        background: $surface;
        border-top: tall $accent 40%;
    }
    TodoPanel.-hidden { display: none; }
    """

    def __init__(self, state: AppState, **kwargs) -> None:
        super().__init__("", **kwargs)
        self._state = state
        self.add_class("-hidden")

    def on_mount(self) -> None:
        self.update_status()

    def update_status(self) -> None:
        s = self._state
        active = s.sessions.get(s.active_session_id) if s.active_session_id else None
        todos = _latest_todos(active)
        if not todos or _all_completed(todos):
            # Empty list or every item ticked off → no working list to
            # show, panel folds away so the chat pane reclaims the row.
            self.add_class("-hidden")
            self.update("")
            return
        markup = _render_items(todos)
        if not markup:
            self.add_class("-hidden")
            self.update("")
            return
        self.remove_class("-hidden")
        self.update(markup)


def _all_completed(todos: list) -> bool:
    """True iff every dict-shaped todo has status == "completed"."""
    items = [t for t in todos if isinstance(t, dict)]
    if not items:
        return False
    return all(t.get("status") == "completed" for t in items)


def _latest_todos(session: SessionState | None) -> list | None:
    """The active session's ACP plan entries, or ``None`` if it has none."""
    if session is None or not session.plan:
        return None
    return session.plan


def _render_items(todos: list) -> str:
    """Build a multi-line markup string. Uses theme tokens so the
    in-progress / completed coloring stays on-theme alongside the rest
    of the app."""
    glyphs = {"completed": "☑", "in_progress": "◐", "pending": "☐"}
    lines: list[str] = []
    for todo in todos:
        if not isinstance(todo, dict):
            continue
        status = str(todo.get("status", "pending"))
        glyph = glyphs.get(status, "·")
        label = (
            todo.get("activeForm")
            if status == "in_progress" and todo.get("activeForm")
            else todo.get("content", "")
        )
        label_text = _escape_markup(str(label))
        if status == "in_progress":
            lines.append(f"[$warning bold]{glyph} {label_text}[/]")
        elif status == "completed":
            lines.append(f"[dim strike]{glyph} {label_text}[/]")
        else:
            lines.append(f"{glyph} {label_text}")
    return "\n".join(lines)


def _escape_markup(text: str) -> str:
    return text.replace("[", r"\[")
