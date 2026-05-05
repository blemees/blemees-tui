"""Console entry point — ``blemees`` and ``python -m blemees_tui``."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from . import __version__


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="blemees",
        description="Multi-session terminal chat for blemees-agentd.",
    )
    p.add_argument("--version", action="version", version=f"blemees-tui {__version__}")
    p.add_argument(
        "--socket",
        default=None,
        help="Override blemees-agentd socket path (also $BLEMEES_AGENTD_SOCKET).",
    )
    p.add_argument(
        "--config",
        default=None,
        help="Override path to config.toml (also $BLEMEES_TUI_CONFIG).",
    )
    p.add_argument(
        "--log-level",
        default=None,
        choices=["debug", "info", "warn", "error"],
        help="Log level for blemees-tui.log (overrides config).",
    )
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    # Defer the heavy import so ``blemees --version`` stays fast.
    from .app import BlemeesTuiApp

    app = BlemeesTuiApp(
        socket_override=args.socket,
        config_path_override=args.config,
        log_level_override=args.log_level,
    )
    app.run()
    return getattr(app, "return_code", 0) or 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
