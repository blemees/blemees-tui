# Changelog

All notable changes to `blemees-tui` will be recorded here. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project tracks [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Per-session snapshot persistence (`snapshots/<session-id>.json`) — TUI
  restart paints the sidebar instantly from cache and the daemon only
  replays frames since the last save (`blemees_tui/snapshot.py`).
- Replay-progress overlay in the chat pane while a session is catching
  up to the daemon's high-water mark.
- UI refresh coalescing for frame bursts — at most one render per Textual
  tick during streaming or replay.
- Per-session composer drafts — unsubmitted text travels with the
  session, not the composer.
- Session header bar showing title, backend/model, and cwd.
- `:select N` TUI command for switching to sessions past `Ctrl+0` (10).
- `Ctrl+0` binds to session 10; `Ctrl+1`–`Ctrl+9` bind to 1–9.
- Pygments-based syntax highlighting for Edit-tool diffs and Write-tool
  content (lexer chosen from the file extension).
- Dedicated rendering for tool blocks with input/result preview.
- Live token-estimate ticker in the per-turn progress row counts tool
  inputs and outputs alongside text.
- `docs/PARITY_BACKLOG.md` — curated list of Claude Code features
  selected for refinement, deferred, or excluded.

### Fixed
- Tool calls now appear in Claude Code sessions (extracted from
  `agent.message.content` rather than relying on suppressed
  `agent.tool_use` frames).
- Tool calls now appear in Codex sessions (reducer no longer crashes on
  list-shaped tool inputs like shell argv).
- Streaming text after a tool call lands in a fresh block instead of
  being appended to the previous (now-stale) text block — fixes interim
  messages disappearing between tools.
- Multiple `agent.message` frames per turn append their content rather
  than overwriting earlier text or clustering tool blocks at the tail.
- Footer correctly shows daemon name and backends after the handshake
  (the `blemeesd.hello_ack` frame now reaches the app's frame handler).
- Footer "turns" count reflects the active session only, not the sum
  across every session.
- Composer focused-state border no longer touches the footer; both
  states use a 1-cell border so layout never shifts on focus.

### Changed
- Composer scoped to the right column under the chat pane only — the
  sidebar runs full height with no input strip beneath it.
- Header bar spans full width (sits above the sidebar) with text padded
  to align over the chat transcript.
- User-prompt rendering uses `❯` instead of `you  >`, with a lighter
  highlight background and 1-row spacing below the prompt.
- Footer uses muted `$panel` background; header keeps the accent colour.
- Connection status dots use `bold bright_*` variants for visibility on
  coloured footer backgrounds.

## [0.1.0] - TBD

Initial alpha release. The feature set documented in
[`docs/SPEC.md`](docs/SPEC.md) milestones M1–M5, including:

- Multi-session sidebar with keyboard navigation.
- Streaming Markdown chat pane with collapsible reasoning + tool blocks.
- Watch mode with one-step take-ownership.
- Reconnect with replay; `replay_gap` banner; `slow_consumer` /
  `oversize_message` / `daemon_shutdown` fatal banners.
- Inline error bubbles for session-scoped errors.
- Footer status chip + event-log overlay + raw-frame debug pane +
  persistent log file.
- New-session modal with collapsible Claude / Codex Advanced sections.
- TUI command set (`:new` `:close` `:delete` `:interrupt` `:rename`
  `:cwd` `:model` `:watch` `:select` `:help` `:q`) with Tab-complete.
- `Ctrl+S` Markdown transcript export.
- Reducer property tests + `Pilot` snapshot tests for primary scenes.

[Unreleased]: https://github.com/blemees/blemees-tui/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/blemees/blemees-tui/releases/tag/v0.1.0
