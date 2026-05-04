# blemees-tui

**Multi-session terminal chat for `blemeesd`** — Claude Code + Codex
agents in one window, with watch mode and persistent transcripts.

> **Status:** v0.1 alpha. Core loop is complete.

## Quick links

- [Spec](SPEC.md) — full v0.1 contract: state model, wire protocol,
  persistence, observability.
- [Implementation plan](IMPLEMENTATION.md) — milestones, what's shipped,
  what's next.
- [Parity backlog](PARITY_BACKLOG.md) — Claude Code features selected for
  refinement, deferred, or excluded.

## Why

Claude Code and Codex are great in their own terminal, but bouncing
between sessions, recovering from a closed shell, or watching a
long-running agent from a second machine all leave you stitching tabs
together by hand. `blemees-tui` is a thin presentation layer over
[`blemeesd`](https://github.com/blemees/blemees-daemon) that keeps every
session live in one window.

## Headline features

- Multi-session sidebar with keyboard navigation.
- Watch mode + one-click take-ownership.
- Streaming Markdown transcript with collapsible reasoning,
  syntax-highlighted Edit diffs, Write previews per language, live token
  estimates.
- Snapshot persistence — TUI restart paints the cached transcript
  instantly; daemon only replays new frames.
- Per-session composer drafts.
- First-class observability: footer chip, event-log overlay, raw-frame
  debug pane, persistent log file.

## Install

See the [README install section](https://github.com/blemees/blemees-tui#install)
for current options. Once published:

```sh
pip install blemees-tui          # PyPI
uv tool install blemees-tui      # uv
pipx install blemees-tui         # pipx
brew install blemees/tap/blemees-tui  # Homebrew (planned)
```

Requires Python ≥ 3.11 and a running
[`blemeesd ≥ 0.9.0`](https://github.com/blemees/blemees-daemon) Unix
socket.

## Build the site locally

```sh
pip install mkdocs mkdocs-material
mkdocs serve     # http://localhost:8000
mkdocs build     # → ./site/
```

GitHub Pages publishing is set up via `gh-deploy` on push to `main` (see
the workflow when added).
