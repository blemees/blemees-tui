"""blemees-tui — Multi-session terminal chat for blemees-agentd.

See ``docs/SPEC.md`` at the repository root for the design contract.
"""

from importlib import metadata as _metadata

try:
    __version__ = _metadata.version("blemees-tui")
except _metadata.PackageNotFoundError:
    __version__ = "0.0.0+unknown"

CLIENT_NAME = f"blemees-tui/{__version__}"
PROTOCOL_VERSION = "blemees/3"

__all__ = ["__version__", "CLIENT_NAME", "PROTOCOL_VERSION"]
