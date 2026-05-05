# blemees-tui

**Multi-session terminal chat for `blemeesd`** — Claude Code + Codex agents,
side-by-side, in one process. Watch a session running in another terminal,
take ownership when you need to type, and never lose a transcript across
restarts.

> **Status:** v0.1 alpha. The core loop is complete; see
> [`docs/IMPLEMENTATION.md`](docs/IMPLEMENTATION.md) for what's done and
> what's next, and [`docs/PARITY_BACKLOG.md`](docs/PARITY_BACKLOG.md) for
> the curated parity backlog.

![blemees-tui screenshot placeholder](docs/screenshot.png)

## Why

Claude Code and Codex are great in their own terminal, but bouncing between
sessions, recovering from a closed shell, or watching a long-running
agent from a second machine all leave you stitching tabs together by hand.
`blemees-tui` is a thin presentation layer over [`blemeesd`](https://github.com/blemees/blemees-daemon)
that keeps every session live in one window:

- **Multi-session sidebar** with keyboard navigation (`Ctrl+1..0`,
  `Ctrl+Tab`, `:select N`).
- **Watch mode** — observe a session running anywhere
  (`blemeesd` is a Unix-socket daemon; SSH-forward it across machines)
  and **take ownership** with one click.
- **Streaming Markdown transcript** with collapsible reasoning,
  syntax-highlighted Edit diffs, Write previews per language, and live
  token estimates.
- **Persistent across restarts** — sessions snapshot to disk after every
  turn; cold start paints from cache and only replays new daemon frames.
- **Per-session drafts** — typed text travels with the session, not the
  composer.
- **First-class observability** — footer chip, event-log overlay
  (`Ctrl+E`), raw-frame debug pane (`Ctrl+D`), persistent log file.

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

Requires Python ≥ 3.11 and a running `blemeesd ≥ 0.9.0` Unix socket.

## Quickstart

```sh
# Auto-resolve the daemon socket (uses $BLEMEESD_SOCKET, then
# $XDG_RUNTIME_DIR/blemeesd.sock, then /tmp/blemeesd-<uid>.sock)
blemees

# Or point explicitly:
blemees --socket /path/to/blemeesd.sock
```

| Action | Keys |
|---|---|
| New session | `Ctrl+N` |
| Attach to existing (watch) | `Ctrl+T` |
| Switch session | `F1`–`F12`, `Ctrl+Tab` |
| Switch to session ≥ 13 | `:select 13` |
| Close current session | `Ctrl+W` |
| Delete current session | `Ctrl+Shift+W` |
| Interrupt turn | `Ctrl+C` |
| Force reconnect | `Ctrl+R` |
| Event log overlay | `Ctrl+E` |
| Debug pane (raw frames) | `Ctrl+D` |
| Save transcript (Markdown) | `Ctrl+S` |
| Toggle reasoning visibility | `t` |
| Help overlay | `?` |
| Quit | `q` |

`/foo` slash commands forward verbatim to the active backend (so Claude
Code skills and Codex slash commands stay reachable). `:foo` is reserved
for TUI commands — see `:help`.

## Configuration

`$XDG_CONFIG_HOME/blemees-tui/config.toml` (or `~/.config/blemees-tui/config.toml`):

```toml
[connection]
socket = ""                    # empty = auto-resolve

[defaults]
backend = ""                   # empty = first advertised by hello_ack
claude_model = "sonnet"
codex_model = "gpt-5.2-codex"
claude_permission_mode = "default"
codex_sandbox = "workspace-write"

[ui]
theme = "dark"                 # dark | light
sidebar_width = 28
show_thinking = false
markdown_code_theme = "monokai"

[logging]
level = "info"                 # debug | info | warn | error
keep_days = 7
```

CLI overrides:

```sh
blemees --socket /tmp/blemeesd.sock --log-level debug
```

Env-var overrides: `BLEMEESD_SOCKET`, `BLEMEES_TUI_THEME`,
`BLEMEES_TUI_LOG_LEVEL`, `BLEMEES_TUI_BACKEND`.

## Persistence

Files under `$XDG_STATE_HOME/blemees-tui/`:

- `sessions.json` — index of live + watching sessions.
- `history.json` — bounded ring (200 entries) of closed-but-remembered sessions.
- `snapshots/<id>.json` — full per-session in-memory state cached to disk
  so a TUI restart skips the full daemon replay.
- `transcripts/` — `Ctrl+S` Markdown exports.
- `blemees-tui.log` — rotating log (weekly, 7 keep).

## Develop

```sh
pip install -e '.[dev]'
pytest                          # 85 tests
ruff check .
ruff format --check .
```

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the workflow.

## Docs

- [`docs/SPEC.md`](docs/SPEC.md) — full v0.1 contract (state model,
  protocol, persistence, observability).
- [`docs/IMPLEMENTATION.md`](docs/IMPLEMENTATION.md) — milestone plan and
  what's shipped.
- [`docs/PARITY_BACKLOG.md`](docs/PARITY_BACKLOG.md) — curated Claude
  Code feature backlog with priorities.

## License

MIT — see [`LICENSE`](LICENSE).
