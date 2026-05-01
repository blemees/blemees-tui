"""Composer — multiline text input (spec §10).

Textual's stock ``TextArea`` consumes Enter (→ newline) and Escape itself
before parent ``on_key`` handlers run. We subclass it with priority
bindings so:

* ``Enter``        → submit (no newline).
* ``Shift+Enter``  → insert newline.
* ``Escape``       → blur (so the app's other shortcuts work again).
* ``Up`` / ``Down`` in an empty composer cycle past user messages.

The submit message bubbles up via ``post_message`` to ``ComposerWidget``,
which re-emits it to the app.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual.widget import Widget
from textual.widgets import TextArea


class ComposerInput(TextArea):
    """Inner TextArea — owns the priority bindings for Enter / Escape /
    arrow keys, and routes those keys to the completion popup when it's
    visible."""

    BINDINGS = [
        Binding("enter", "submit", show=False, priority=True),
        Binding("shift+enter", "insert_newline", show=False, priority=True),
        Binding("escape", "blur", show=False, priority=True),
        Binding("up", "history_up", show=False, priority=True),
        Binding("down", "history_down", show=False, priority=True),
        Binding("tab", "tab", show=False, priority=True),
    ]

    class Submit(Message):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    class HistoryStep(Message):
        def __init__(self, direction: int) -> None:
            super().__init__()
            self.direction = direction

    # ------------------------------------------------------------------
    # Popup integration
    # ------------------------------------------------------------------

    def _popup(self):
        try:
            from .completion import CompletionPopup

            return self.app.query_one("#completion", CompletionPopup)
        except Exception:
            return None

    def _popup_visible(self) -> bool:
        popup = self._popup()
        return popup is not None and popup.is_visible()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_submit(self) -> None:
        # Enter accepts the popup selection if one is open.
        popup = self._popup()
        if popup is not None and popup.is_visible():
            popup.accept()
            return
        text = self.text.rstrip("\n")
        if not text:
            return
        self.clear()
        # Stay focused — the user is mid-conversation and shouldn't have to
        # re-focus before the next message. ESC blurs explicitly.
        self.focus()
        self.post_message(self.Submit(text))

    def action_insert_newline(self) -> None:
        self.insert("\n")

    def action_blur(self) -> None:
        # Esc dismisses the popup first; keeps composer focus.
        popup = self._popup()
        if popup is not None and popup.is_visible():
            popup.dismiss()
            return
        self.app.set_focus(None)

    def action_tab(self) -> None:
        # Tab accepts the popup selection when one is open; otherwise it
        # behaves like a normal text-editor Tab (insert four spaces). The
        # composer is already focused, so Tab has no focus role here —
        # Esc is the explicit deactivate.
        popup = self._popup()
        if popup is not None and popup.is_visible():
            popup.accept()
            return
        self.insert("    ")

    def action_history_up(self) -> None:
        if self._popup_visible():
            self._popup().step(-1)
            return
        if not self.text:
            self.post_message(self.HistoryStep(-1))
        else:
            self.action_cursor_up()

    def action_history_down(self) -> None:
        if self._popup_visible():
            self._popup().step(+1)
            return
        if not self.text:
            self.post_message(self.HistoryStep(+1))
        else:
            self.action_cursor_down()


class ComposerWidget(Widget):
    """Multiline input. Starts at one line and grows as content adds lines.

    Capped at 12 lines so a long paste doesn't push the chat pane offscreen.
    ``Enter`` sends; ``Shift+Enter`` inserts a newline.
    """

    DEFAULT_CSS = """
    ComposerWidget {
        height: auto;
        margin-top: 1;
    }
    ComposerWidget ComposerInput {
        /* Muted accent — same colour family as the focused state, faded
           to ~40% so the relationship reads "active vs inactive". */
        border: round $accent 40%;
        height: auto;
        min-height: 3;
        max-height: 14;
        padding: 0 1;
    }
    ComposerWidget ComposerInput:focus {
        border: round $accent;
    }
    """

    class Submit(Message):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    class Changed(Message):
        """Posted whenever the composer text changes (drives the
        completion popup)."""

        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._recall: list[str] = []
        self._recall_index: int | None = None

    def compose(self) -> ComposeResult:
        yield ComposerInput(id="composer-input")

    def set_text(self, text: str, *, focus: bool = True) -> None:
        ta = self.query_one("#composer-input", ComposerInput)
        ta.text = text
        if focus:
            ta.focus()
        try:
            ta.move_cursor((0, len(text)))
        except Exception:
            pass

    def on_text_area_changed(self, _event) -> None:  # noqa: ANN001
        # Textual fires TextArea.Changed (built-in) on every edit. Bubble
        # it up so the app can drive the completion popup.
        try:
            ta = self.query_one("#composer-input", ComposerInput)
            self.post_message(self.Changed(ta.text))
        except Exception:
            pass

    def set_recall_history(self, history: list[str]) -> None:
        self._recall = list(history)
        self._recall_index = None

    def on_composer_input_submit(self, msg: ComposerInput.Submit) -> None:
        self._recall_index = None
        self.post_message(self.Submit(msg.text))

    def on_composer_input_history_step(self, msg: ComposerInput.HistoryStep) -> None:
        self._recall_step(msg.direction)

    def _recall_step(self, direction: int) -> None:
        if not self._recall:
            return
        ta = self.query_one("#composer-input", ComposerInput)
        if self._recall_index is None:
            self._recall_index = len(self._recall) if direction < 0 else -1
        new_index = max(0, min(len(self._recall) - 1, self._recall_index + direction))
        if direction > 0 and new_index == len(self._recall) - 1 and self._recall_index == new_index:
            ta.clear()
            self._recall_index = None
            return
        self._recall_index = new_index
        ta.text = self._recall[self._recall_index]

    def set_enabled(self, enabled: bool) -> None:
        ta = self.query_one("#composer-input", ComposerInput)
        was_disabled = ta.disabled
        ta.disabled = not enabled
        if enabled and was_disabled:
            # Coming back to life after a turn — refocus so the user can
            # immediately keep typing without hitting `/` or clicking.
            ta.focus()
