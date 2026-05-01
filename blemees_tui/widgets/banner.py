"""Top-of-screen connection banner (spec §6.3, §6.6, §15.1).

A single Static row that's hidden when ``connection_status == "connected"``
and the app has no pending fatal-class daemon error. The app feeds it via
``set_connection(...)`` and ``set_fatal(...)``.
"""

from __future__ import annotations

from textual.widgets import Static


class ConnectionBanner(Static):
    DEFAULT_CSS = """
    ConnectionBanner { dock: top; height: auto; padding: 0 1; background: $warning-darken-2; color: $text; }
    ConnectionBanner.-hidden { display: none; }
    ConnectionBanner.-fatal { background: $error-darken-1; }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__("", **kwargs)
        self.add_class("-hidden")
        self._fatal_text: str = ""

    def set_connection(
        self,
        *,
        state: str,
        attempt: int,
        next_in_ms: int,
        last_error: str,
    ) -> None:
        if self._fatal_text:
            return  # fatal text wins until cleared
        if state == "connected":
            self.add_class("-hidden")
            self.remove_class("-fatal")
            self.update("")
            return
        self.remove_class("-hidden")
        if state == "reconnecting":
            secs = max(1, next_in_ms // 1000)
            err = f" — {last_error}" if last_error else ""
            self.update(f"[b]Reconnecting…[/]  attempt {attempt}, next in {secs}s{err}")
        elif state == "fatal":
            self.add_class("-fatal")
            self.update(f"[b]Daemon protocol mismatch.[/]  {last_error}")
        else:
            self.update(f"[b]Disconnected.[/]  {last_error}")

    def set_fatal(self, text: str) -> None:
        """Latch a connection-fatal banner (slow_consumer / oversize_message
        / daemon_shutdown) until ``clear_fatal()``."""
        self._fatal_text = text
        if not text:
            self.add_class("-hidden")
            self.remove_class("-fatal")
            self.update("")
            return
        self.remove_class("-hidden")
        self.add_class("-fatal")
        self.update(f"[b]Connection error:[/]  {text}")

    def clear_fatal(self) -> None:
        self.set_fatal("")
