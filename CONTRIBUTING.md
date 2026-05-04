# Contributing to blemees-tui

Thanks for considering a contribution. The TUI is small, focused, and
strives to stay that way — every feature lands with tests and an entry in
[`docs/PARITY_BACKLOG.md`](docs/PARITY_BACKLOG.md) or
[`docs/IMPLEMENTATION.md`](docs/IMPLEMENTATION.md). If you're not sure a
change fits, open an issue first.

## Architecture in 30 seconds

`blemees-tui` is a thin presentation layer over the `blemeesd` daemon.
The daemon handles all agent execution (Claude Code, Codex, MCP, hooks,
sub-agents); the TUI's job is to **drive sessions and render their event
streams legibly**. When in doubt, surface daemon events well rather than
reimplement them.

Core layers:

| Layer | Purpose |
|---|---|
| `state.py` | Pure dataclasses — session/turn/block model. |
| `reducer.py` | Pure `(SessionState, frame) → SessionState`. |
| `connection.py` | Async Unix-socket multiplexer; reconnect with backoff; per-session inboxes. |
| `persistence.py` + `snapshot.py` | Atomic JSON I/O: sessions index, history, per-session snapshots. |
| `app.py` | Textual App that wires the layers together. |
| `widgets/` | Pure rendering — sidebar, chat pane, composer, footer, modals, debug pane. |

Pure layers (`state`, `reducer`, `persistence`, `snapshot`,
`commands`, `discover`, `transcript`, `config`) have no Textual
dependency. Keep them that way — the unit tests rely on it.

## Setup

```sh
git clone https://github.com/blemees/blemees-tui
cd blemees-tui
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
```

Requires Python ≥ 3.11 and a running `blemeesd ≥ 0.9.0` if you want to
exercise the live socket path.

## Run the tests

```sh
pytest                          # full suite (~85 tests, ~2 s)
pytest tests/test_reducer.py    # one file
pytest -k snapshot              # one keyword
```

`tests/test_app_pilot.py` uses Textual's `Pilot` harness for snapshot-style
scene tests; they don't open a real socket.

## Lint & format

```sh
ruff check .
ruff format --check .           # check
ruff format .                   # apply
```

CI (when added) will run both.

## Commit / PR conventions

- Keep commits focused. A bug fix is one commit; a feature with three
  refactors stretching across the codebase is several.
- Subject lines: imperative mood, ≤ 72 chars (e.g. `fix tool calls
  vanishing on multi-message turns`).
- Body: explain the *why* and link to the issue / spec section if
  relevant.
- Add a regression test for every bug fix and a unit test for every new
  pure function.
- Update `CHANGELOG.md` under `## [Unreleased]` for any user-visible
  change.
- Update `docs/IMPLEMENTATION.md` when finishing a milestone item.

## Adding a feature

1. Find the item in `docs/PARITY_BACKLOG.md` (or open an issue first to
   propose a new one). The backlog records open questions; resolve them
   before coding.
2. Promote the item into `docs/IMPLEMENTATION.md` as an M6/M7 numbered
   task.
3. Land the change with tests, a changelog entry, and any spec edits.

## Spec & protocol

The wire protocol the TUI speaks is documented in `blemees-daemon` (the
`blemees/2` protocol). The TUI's contract — state model, persistence
shape, observability — lives in [`docs/SPEC.md`](docs/SPEC.md). When
behaviour changes, edit the spec first and the code second.

## License

By contributing you agree your work is licensed under the project's MIT
license.
