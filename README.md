# blemees-tui — Multi-session terminal chat for blemeesd

**Status:** v0.1 alpha — scaffold. See [`docs/SPEC.md`](docs/SPEC.md) for the contract.

`blemees` is a Textual-based terminal chat UI that talks to a local
[`blemeesd`](https://github.com/blemees/blemees-daemon) daemon and hosts
multiple chat sessions in one process. Each session runs against a chosen
backend (Claude Code or Codex) inside the daemon; the TUI is a thin
presentation layer.

## Install

```sh
# PyPI (when published)
pip install blemees-tui

# uv tool
uv tool install blemees-tui

# pipx
pipx install blemees-tui

# Homebrew (planned)
brew install blemees/tap/blemees-tui
```

Requires Python 3.11+ and a running `blemeesd` ≥ 0.9.0 socket.

## Run

```sh
blemees                    # auto-resolve socket
blemees --socket /path/blemeesd.sock
```

## Develop

```sh
pip install -e '.[dev]'
pytest
ruff check .
```

## License

MIT — see [`LICENSE`](LICENSE).
