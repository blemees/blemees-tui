# blemees-tui — Multi-session terminal chat for blemees-agentd

**Version:** 0.1 (spec)
**Targets:** `blemees-agent/1` protocol. Daemon ≥0.9.0 is required for the
extended `list_sessions` filter shape and the `session_closed`
watcher-side frame (§16). No protocol-version bump — the daemon's
0.9.0 changes are additive on `blemees-agent/1`.
**Language:** Python 3.11+
**Runtime deps:** `blemees>=0.9.0`, `textual>=0.80`
**Target OS:** Linux, macOS

This document is the contract for `blemees-tui` v0.1. Implementation
details live in code; design intent and behavior live here. Scaffolding
follows once this spec is signed off.

---

## 1. Overview

`blemees` is a terminal chat UI that talks to a local `blemees-agentd` daemon
and hosts multiple chat sessions in one process. Each session runs
against a chosen backend (Claude Code or Codex) inside the daemon; the
TUI is a thin presentation layer.

The TUI:

- Holds **one** socket connection to `blemees-agentd`.
- Multiplexes any number of live chat sessions over it.
- Reattaches across TUI restarts via `resume:true` + stored `last_seen_seq`.
- Renders the unified `agent.*` event vocabulary so Claude and Codex
  sessions look the same.
- Supports **watch mode** for read-only observation of sessions driven
  by automation, with one-step take-ownership.

It is **not** a replacement for `blemeesctl` (the wire-protocol probe;
see §16.3). The TUI assumes a working daemon and at least one backend
on `$PATH`.

---

## 2. Goals (v0.1)

- Open / drive / interrupt / close / delete sessions on either backend.
- Streaming Markdown rendering with collapsible tool blocks and reasoning.
- Multi-session sidebar with keyboard navigation.
- Watch mode for live remote observation; one-step take-ownership.
- Robust reconnect with replay; gap detection.
- First-class observability: footer chip, event-log overlay, persistent
  log, optional raw-frame debug pane.
- Configurable theme + keybindings via TOML.
- Distribution via PyPI, `uv tool`, `pipx`, Homebrew.

## 3. Non-goals (v0.1)

- Starting / managing the daemon process.
- Authentication management (no `claude /login` / `codex login` from
  the TUI; surface the error and tell the user to run the upstream CLI).
- Multimodal input (Claude images, Codex attachments).
- Profiles / per-cwd auto-defaults.
- Side-by-side compare, historical browser, search, JSONL export,
  plugins, custom themes.

---

## 4. Architecture

```
┌──────────────── blemees TUI (Textual App) ──────────┐
│                                                      │
│  Widgets                                             │
│   ├─ SidebarWidget       (sessions list)             │
│   ├─ ChatPaneWidget      (active session transcript) │
│   ├─ ComposerWidget      (multiline input)           │
│   ├─ FooterStatusWidget                              │
│   ├─ EventLogOverlay     (Ctrl+E)                    │
│   └─ DebugPane           (Ctrl+D)                    │
│                                                      │
│  AppState (in-memory)                                │
│   ├─ Connection          (BlemeesClient wrapper)     │
│   ├─ SessionStore        { id → SessionState }       │
│   ├─ EventLog            (ring of 2000 entries)      │
│   └─ Config                                          │
│                                                      │
│  Persistence ($XDG_STATE_HOME/blemees/tui/)         │
│   ├─ sessions.json       (live + watching)           │
│   ├─ history.json        (closed-but-remembered)     │
│   └─ tui.log     (rotating)                  │
│                                                      │
└──────────────────────────────────────────────────────┘
                         │ AF_UNIX
                         ▼
                 blemees-agentd (separate process)
```

`Connection` is a thin wrapper around `blemees.client.BlemeesClient`
that adds:

- One inbox per `session_id` (plus a watcher inbox).
- A reconnect loop that re-issues `open … resume:true` (or `watch`)
  for every known session.
- A central event-log feed.

`SessionState` is a reducer fed `agent.*` and per-session `blemees-agentd.*`
frames. It owns:

- Turns (each turn: user message + assistant content blocks + tool
  blocks + result usage).
- Title, backend, cwd, model, options.
- `mode`: `owned` | `watching` | `detached` | `crashed` | `closed`.
- Counters: cumulative usage, `last_seq`, `last_seen_seq`, `last_active_at_ms`.

The reducer is **pure**: `(SessionState, Frame) → SessionState`. This
makes it straightforward to unit-test exhaustively and to replay
durable logs deterministically.

---

## 5. Identity & distribution

| Field           | Value                                                                                |
|-----------------|--------------------------------------------------------------------------------------|
| Repo            | `github.com/blemees/blemees-tui`                                                     |
| PyPI            | `blemees-tui`                                                                        |
| Console script  | `blemees`                                                                            |
| License         | MIT                                                                                  |
| Python          | `>=3.11`                                                                             |
| Runtime deps    | `blemees>=0.9.0`, `textual>=0.80`                                                    |
| Channels        | PyPI, `uv tool install blemees-tui`, `pipx install blemees-tui`, `brew install blemees-tui` |

The console script `blemees` is owned by the TUI from v0.1 onward.
The daemon's existing wire-probe REPL was renamed to `blemeesctl` in
daemon 0.9.0 (§16.3) — a clean break: daemon 0.9.0 ships only
`blemees-agentd` and `blemeesctl`, leaving `blemees` free for the TUI to
claim. No deprecation alias was used because any alias on the daemon
side would have collided with this package's claim on the name.

---

## 6. Connection model

### 6.1 Socket resolution

Same precedence as `BlemeesClient.connect()`:

1. `$BLEMEES_AGENTD_SOCKET` (and CLI `--socket <path>`).
2. `$XDG_RUNTIME_DIR/blemees/agentd.sock`.
3. `/tmp/blemees-agentd-<uid>.sock`.

### 6.2 Handshake

```json
{"type":"agent.hello","client":"blemees-tui/<version>","protocol":"blemees-agent/1"}
```

Store `hello_ack.backends` for the new-session modal's backend picker
(only offer backends the daemon advertises).

### 6.3 Reconnect

On socket loss:

- Backoff: 1s → 30s cap, multiplier 1.5, jitter ±20%, indefinite.
- Banner: `"reconnecting (attempt N, next in Ms)…"`.
- On success:
  1. Send `hello`.
  2. For each known live session, `open … resume:true, last_seen_seq:<stored>`.
  3. For each known watch, `watch … last_seen_seq:<stored>`.
  4. `session_unknown` → strip from live list, add to history,
     event log entry at `info`.

### 6.4 Liveness

Send `ping` every 15s while idle (no inbound for ≥10s). Round-trip
shown in debug pane (`Ctrl+D`); not in main UI.

### 6.5 Fatal protocol mismatch

`agent.error{code:"protocol_mismatch"}` → modal `"daemon speaks
blemees/X, blemees-tui requires blemees/Y. Upgrade one."` and exit 2.
No negotiation.

### 6.6 Slow consumer / oversize / shutdown

`agent.error{code:"slow_consumer"|"oversize_message"|"daemon_shutdown"}`
is fatal to the connection. Reconnect loop handles it; banner
explains the cause.

---

## 7. Session lifecycle

### 7.1 Create

`Ctrl+N` opens **New session** modal:

- **Backend** (radio, only `hello_ack.backends` entries).
- **Model** (preset list per backend; free-text override).
- **cwd** (default `$PWD`; `?` opens autocomplete).
- **Title** (optional; auto-derived from first user message if empty).
- **Advanced** (collapsible):
  - Claude: `permission_mode`, `tools`, `disallowed_tools`,
    `system_prompt`, `mcp_config`, `effort`, `agent`, `betas`.
  - Codex: `sandbox`, `approval-policy`, `developer-instructions`,
    `base-instructions`, `compact-prompt`, `config` (raw TOML/JSON).

Submit → `agent.open{session_id:uuid4(), backend, options}`.

### 7.2 Send / receive

- `Enter` → `agent.user{session_id, message:{role:"user",content:<text>}}`.
- Composer disabled while `turn_active = True` (between send and
  `agent.result`). Spinner shown next to the active message.
- The TUI never relies on `user_echo`; it renders the user's own
  message locally when sending, regardless of backend echo settings.

### 7.3 Interrupt

`Ctrl+C` → `agent.interrupt`. Daemon emits
`agent.result{subtype:"interrupted"}`; UI re-enables composer.
A second `Ctrl+C` within 1s opens a quit-confirm modal.

### 7.4 Close vs delete

| Key             | Action                | Effect                                                                                                                            |
|-----------------|-----------------------|-----------------------------------------------------------------------------------------------------------------------------------|
| `Ctrl+W`        | Close (keep log)      | `agent.close{delete:false}`. Session moves to history pane. Reattachable via daemon log replay.                                |
| `Ctrl+Shift+W`  | Delete (with confirm) | `agent.close{delete:true}`. Daemon unlinks event log + usage sidecar. Backend native transcript is **not** touched (per spec). |

### 7.5 Persisted state across TUI restarts

`$XDG_STATE_HOME/blemees/tui/sessions.json`:

```jsonc
{
  "version": 1,
  "sessions": [
    {
      "session_id": "5a01...",
      "backend": "claude",
      "model": "sonnet",
      "cwd": "/home/u/proj",
      "title": "refactor utils.py",
      "options": { "claude": { ... } },
      "last_seen_seq": 47,
      "last_active_at_ms": 1745000000000,
      "mode": "owned"
    }
  ]
}
```

On launch:

1. Read `sessions.json`.
2. Connect + handshake.
3. For each entry, attempt `open … resume:true` (`watch` for watching
   ones) with stored `last_seen_seq`.
4. `session_unknown` → strip from live list, log, push to history.
5. Live entries → restored.

`sessions.json` is rewritten on every state change and on graceful
shutdown.

### 7.6 Session takeover

`agent.session_taken{by_peer_pid}` → flip the pane into a banner:

```
Session was taken over by pid 12345 at 14:02. [Reclaim] [Move to history]
```

`Reclaim` sends `open … resume:true` (which will in turn notify the
new owner with `session_taken`). The TUI does **not** arbitrate
ping-pong; that's user judgment.

### 7.7 Concurrent turns

The daemon enforces `session_busy`. The TUI prevents user-triggered
violations by disabling the composer while `turn_active`. If
`session_busy` arrives anyway (e.g. race after reconnect), surface as
a soft toast and refresh composer state from the next `agent.result`.

---

## 8. Watch mode (v0.1)

### 8.1 Discovery

Two paths in the **Attach** modal (`Ctrl+T`):

- **Pick from list** (primary): TUI calls `agent.list_sessions`
  with `live:true` (§16.1) and renders a sortable table over the
  returned `sessions` array, using only the rows whose `attached`,
  `started_at_ms`, etc. fields the daemon populates for live entries:

  | session_id (short) | backend | cwd       | title              | owner_pid | started     | last_active |
  |--------------------|---------|-----------|--------------------|-----------|-------------|-------------|
  | `5a01…`            | claude  | `/proj`   | refactor utils.py  | 12345     | 14:02       | 14:14       |

  The TUI uses `live:true` (no `cwd`) for the global picker. A future
  per-project picker would pass `live:true, cwd:$PWD` to scope. The
  TUI **always** sends the explicit `live:true` flag rather than an
  empty body — empty means "every session, everywhere" (the daemon
  walks all disk transcripts as well), which is intended for a future
  historical browser, not the watch picker.

- **Paste id**: free-text input; UUID-validated before submit.

### 8.2 Subscribe

`agent.watch{session_id, last_seen_seq:0}` to request full ring
replay. With the daemon's durable event log enabled, this
reconstructs the entire session; without it, only the buffer tail.
A `agent.replay_gap` arriving in the replay drives a banner so the
user knows context is incomplete.

### 8.3 Render

- Sidebar: 👀 icon next to title; right-click reveals owner pid + cwd
  + backend.
- Pane header banner:

  ```
  👀 Watching · owner pid 12345 · started 14:02:11
  ```

- **Composer hidden**, replaced with a row: `[Take ownership]` `[Stop watching]`.
- All other rendering identical to owned sessions.

### 8.4 Take ownership

`[Take ownership]` sends `agent.open{session_id, resume:true}`.
Daemon notifies the previous owner with `session_taken`. On `opened`
reply, TUI flips `mode: watching → owned`, swaps button row for
composer, persists to `sessions.json`.

### 8.5 Owner-side close

When the owner sends `agent.close`, daemon emits
`agent.session_closed{session_id, reason:"owner_closed"}` (§16.2)
to all watchers. TUI flips the pane to a closed-state banner:

```
Session closed by owner at 14:23. [Move to history]
```

### 8.6 Stop watching

`[Stop watching]` → `agent.unwatch`. UI removes pane from sidebar
(or moves to history if `ui.history_on_unwatch = true`, default `false`).

### 8.7 Reconnect

Watch entries in `sessions.json` are restored via
`agent.watch{last_seen_seq:<stored>}` on reconnect. `session_unknown`
→ strip + log.

---

## 9. Rendering

### 9.1 Transcript composition

A session's transcript is a list of **turns**. Each turn contains, in
order:

- 1× `agent.user` (rendered as user bubble; we render locally, no
  reliance on `user_echo`).
- 0..N assistant content blocks: text (live-built from `agent.delta`,
  finalised by `agent.message`), tool_use, thinking.
- 0..N `agent.tool_result` (matched to their `tool_use_id`).
- 1× `agent.result` (renders as a stat divider).

### 9.2 Streaming text

`agent.delta{kind:"text"}` appends to the in-progress assistant
message. On `agent.message` we replace the in-progress block with the
canonical content (deltas may carry orderings the daemon doesn't
fully normalise). On `agent.result` we lock the turn.

### 9.3 Reasoning / thinking

`agent.delta{kind:"thinking"}` accumulates into a foldable
"🧠 reasoning" block placed above the next text/tool block of the
same turn. Collapsed by default. `t` toggles globally; per-turn click
overrides global.

### 9.4 Tool use

`agent.tool_use{tool_use_id, name, input}`:

```
▸ Read(path="/home/u/proj/utils.py")
```

`Enter` or click expands:

```
▾ Read
   input:  { "path": "/home/u/proj/utils.py" }
   result: <2.4 KiB elided — Enter to view>
```

`agent.tool_result` matched by `tool_use_id`; if `is_error`, the
block goes red.

### 9.5 Markdown

Assistant text is parsed via Textual's `Markdown` widget. Code blocks
use Pygments (Textual's bundled lexers); language inferred from fence
info string. Theme configurable via `ui.markdown_code_theme`.

### 9.6 Notices

- `agent.notice{category:"rate_limits"}` → footer chip
  (e.g. `↺ resets in 4m`). Yellow on `level:"warn"`.
- All other notices → event log overlay only (not transcript).

### 9.7 Result divider

```
─── 1.4s · ↑320 ↓580 · ctx 14k/200k ──────────────────
```

Built from `agent.result.duration_ms` and the normalised `usage`.
`reasoning_output_tokens` (Codex) shown as `🧠 200` when non-zero.

### 9.8 Inline error rendering

Errors carrying a `session_id` for the active session render as a red
bubble at their wall-clock position **and** land in the event log:

- `auth_failed` — message + per-backend instruction text.
- `backend_crashed` — message + `[Re-open]` button.
- `agent.result{subtype:"error", error:{code,message}}` — message;
  no inline button.

---

## 10. Input

| Element            | Behavior                                                                                       |
|--------------------|------------------------------------------------------------------------------------------------|
| Composer           | Multiline `TextArea`, fixed height 4 rows, expands to 12 max.                                  |
| Send               | `Enter`                                                                                        |
| Newline            | `Shift+Enter`                                                                                  |
| Submit-and-send    | `Ctrl+Enter` (alias for Enter)                                                                 |
| History recall     | `Up`/`Down` in **empty** composer cycles past user messages of current session.                |
| TUI commands       | Vim-style ``:`` prefix: `:new`, `:close`, `:delete`, `:interrupt`, `:rename`, `:cwd <path>`, `:model <name>`, `:watch <id>`, `:help`, `:q`/`:quit`. Tab-completion for known forms. ``/``-prefixed input is forwarded verbatim to the active backend so Claude Code skills + Codex slash commands work unchanged. |
| Bracketed paste    | Large pastes go in as a single block, no premature send.                                       |

---

## 11. Layout & keybindings

```
┌─ Sessions ─────────┬─ proj-a · claude · sonnet ──────────────────────┐
│ + New (Ctrl+N)     │ you  > refactor utils.py                          │
│ + Attach (Ctrl+T)  │ ▸ Read utils.py                                   │
│ profile: default   │ ▸ Edit utils.py                                   │
│ ● developer (1)    │ ai  > Done. Renamed `frob()` → `frobnicate()`.   │
│   1 ● build-api ●  │ ─── 1.4s · ↑320 ↓580 · ctx 14k/200k ────────── │
│ ● architect (1)    │                                                    │
│   2 👀 ci-deploy   │ ┌─ compose (Enter to send) ───────────────────┐ │
│ ○ tester           │ │ _                                            │ │
│                    │ └──────────────────────────────────────────────┘ │
├────────────────────┴─────────────────────────────────────────────────┤
│ ● daemon 0.9.0 · claude 2.1 codex 0.125 · 5 turns · ctx 14k/200k · ! │
└──────────────────────────────────────────────────────────────────────┘
```

Sessions are scoped to the active profile (`--profile`, else the first
configured profile) and nested under their agent: each roster agent is a
header (dimmed `○` when it has no sessions yet, `●` with a live count when it
does), and that agent's sessions are indented beneath it with the numeric
index used by `1`..`9` / `:select`. Sessions belonging to other profiles stay
in the registry (reachable via `:attach`) but are not listed or indexed here.

| Key                              | Action                                       |
|----------------------------------|----------------------------------------------|
| `Ctrl+N`                         | New session modal                            |
| `Ctrl+T`                         | Attach (watch) modal                         |
| `1`..`9`                         | Switch to session N                          |
| `Ctrl+Tab` / `Ctrl+Shift+Tab`    | Next / prev session                          |
| `Ctrl+W`                         | Close current session                        |
| `Ctrl+Shift+W`                   | Delete current session                       |
| `Ctrl+C`                         | Interrupt turn (twice within 1s = quit-confirm) |
| `Ctrl+R`                         | Force reconnect                              |
| `Ctrl+E`                         | Event log overlay                            |
| `Ctrl+D`                         | Debug pane (raw frames)                      |
| `Ctrl+L`                         | Re-render screen                             |
| `Ctrl+S`                         | Save current transcript to `.md`             |
| `r`                              | Rename session                               |
| `t`                              | Toggle thinking visibility                   |
| `q`                              | Quit (with confirm if turns in flight)       |
| `?`                              | Help overlay                                 |
| `/`                              | Focus composer                               |

Mouse on. Sidebar resizable via drag. Theme: dark default, light via
config. All keybindings overridable via `[keybindings]` in config.

---

## 12. Configuration

Path: `$XDG_CONFIG_HOME/blemees/tui.toml`
(or `~/.config/blemees/tui.toml`).

```toml
[connection]
socket = ""                    # empty = auto-resolve

[defaults]
backend = ""                   # empty = first advertised by hello_ack
claude_model = "sonnet"
codex_model = "gpt-5.2-codex"
claude_permission_mode = "default"
codex_sandbox = "workspace-write"
cwd = ""                       # empty = $PWD at launch

[ui]
theme = "dark"                 # dark | light
sidebar_width = 28
show_thinking = false
history_on_unwatch = false
markdown_code_theme = "monokai"

[logging]
level = "info"                 # debug | info | warn | error
keep_days = 7

[keybindings]
# Override any from §11. Example:
# new_session = "ctrl+n"
# interrupt = "ctrl+c"
```

Override precedence: env vars (`BLEMEES_TUI_*`) > CLI flags > config
file > built-in defaults.

---

## 13. Persistence (TUI-side)

| Path                                              | Purpose                                                                       |
|---------------------------------------------------|-------------------------------------------------------------------------------|
| `$XDG_STATE_HOME/blemees/tui/sessions.json`       | Live + watching sessions known to TUI. Rewritten on every state change.       |
| `$XDG_STATE_HOME/blemees/tui/history.json`        | Closed-but-remembered sessions. Bounded 200 entries.                           |
| `$XDG_STATE_HOME/blemees/tui/tui.log`     | Rotating log (weekly, 7 keep).                                                |
| `$XDG_STATE_HOME/blemees/tui/transcripts/`        | `Ctrl+S` exports. Filename: `<title-slug>-<short-id>.md`.                      |

Atomic writes for JSON files: write to `<file>.tmp`, `fsync`, rename.

JSONL transcript export deferred to v0.2.

---

## 14. Observability

Three surfaces, each does one job.

### 14.1 Footer status chip

Always visible:

```
● daemon X.Y · claude V codex V · N turns · ctx Tk/Mk · ! N errors · ↺ rate
```

| Element                | Source                                                              |
|------------------------|---------------------------------------------------------------------|
| Connection dot         | green (connected), yellow (reconnecting), red (down).               |
| Daemon + backend vers. | `hello_ack.backends`.                                               |
| Turns                  | Sum of all live sessions' turn counters.                            |
| Ctx                    | Active session's `context_tokens` from `session_info`.              |
| `! N errors`           | Unacknowledged daemon errors; click → event log filtered to errors. |
| `↺ rate`               | Most-recent `agent.notice{rate_limits}` summary.                    |

### 14.2 Event log overlay (`Ctrl+E`)

```
[All] [Daemon errors] [Daemon stderr] [Notices] [TUI internal] [Connection]
─────────────────────────────────────────────────────────────────────────
HH:MM:SS.mmm  source        session  category           message
─────────────────────────────────────────────────────────────────────────
[c] copy line · [C] copy all visible · [/] filter · [s] save log · [esc] close
```

Sources:

- `daemon-error` — `agent.error` frames (all codes).
- `daemon-stderr` — `agent.stderr` lines (rate-limited at daemon).
- `notice` — `agent.notice` frames.
- `tui-internal` — TUI exceptions, render warnings, parse failures,
  queue stalls.
- `connection` — hello, opens, closes, takeovers, replay gaps,
  daemon shutdown.

In-memory ring of 2000 entries; the persistent log holds the full
history regardless.

### 14.3 Persistent log

`$XDG_STATE_HOME/blemees/tui/tui.log`. Always written. Format:
one structured event per line with timestamp, level, source,
session_id, message, context. `tail -f` friendly.

### 14.4 Debug pane (`Ctrl+D`)

Last 200 wire frames in **+** out, JSON-formatted with timestamp.
Off by default; opt-in. Mirrors `blemeesctl` for live debugging
without leaving the TUI.

### 14.5 Inline error rendering

Errors carrying a `session_id` for the active session render as a red
bubble in the transcript at their wall-clock position. See §9.8.

---

## 15. Errors / edge-case handling

| Daemon-side condition                  | TUI behavior                                                           |
|----------------------------------------|------------------------------------------------------------------------|
| `protocol_mismatch`                    | Fatal modal, exit 2.                                                   |
| `invalid_message` / `unknown_message`  | Should be unreachable for normal flows; log as `tui-internal` warning. |
| `unknown_backend`                      | New-session modal validates; if seen, event log.                       |
| `unsafe_flag`                          | Blocked at modal level; if seen, event log.                            |
| `session_unknown`                      | On resume → strip from `sessions.json`. On send → mark crashed.        |
| `session_exists`                       | Generate new uuid and retry once.                                      |
| `session_busy`                         | Soft toast; refresh composer state from next `agent.result`.           |
| `spawn_failed`                         | Inline red bubble + toast.                                             |
| `backend_crashed`                      | Inline red bubble; mode `crashed`; offer `[Re-open]`.                  |
| `auth_failed`                          | Inline red bubble + sidebar tag; per-backend instruction text.         |
| `oversize_message`                     | Fatal connection close → reconnect loop. Event log entry.              |
| `slow_consumer`                        | Same.                                                                  |
| `daemon_shutdown`                      | Banner; reconnect loop kicks in once daemon returns.                   |
| `internal`                             | Event log + inline bubble.                                             |
| `replay_gap`                           | Banner inside affected session.                                        |
| `session_taken`                        | Banner with `[Reclaim]` / `[Move to history]`.                         |
| `session_closed` (watcher)             | Banner with `[Move to history]`.                                       |

### 15.1 Daemon down at launch

Friendly screen: `"Can't reach blemees-agentd at <socket>. Reconnecting…"`.
Background reconnect loop. `Ctrl+R` retries sooner. `?` modal lists
common fixes (`systemctl --user start blemees-agentd`, `brew services start
blemees`, etc.).

### 15.2 Backend missing from daemon

`hello_ack.backends` lacks `claude` or `codex` → New-session modal
hides that backend with a footnote: `"codex not detected by daemon"`.

### 15.3 Single-instance posture

The TUI does **not** enforce single-instance. Two TUIs against the
same daemon is supported (and is one of the points). Session takeover
between TUI instances works exactly as between any clients.

---

## 16. Daemon-side dependencies

All three landed in **daemon 0.9.0** as additive changes on
`blemees-agent/1` — no protocol-version bump. The TUI requires
`blemees>=0.9.0` at install.

### 16.1 `agent.list_sessions` filter shape

The existing `agent.list_sessions` verb was extended; **no new
verb was added**. `cwd` and `live` are independent, fully-composable
filters — omitting a filter means "no filter on that axis":

| `cwd` | `live`  | Behavior                                                                                                          |
|-------|---------|--------------------------------------------------------------------------------------------------------------------|
| set   | omitted | On-disk transcripts merged with live overlay for that cwd. Original v0.1 contract.                                |
| set   | `true`  | Live sessions only, scoped to that cwd.                                                                            |
| set   | `false` | Cold (on-disk-only) sessions for that cwd; excludes anything currently live.                                       |
| absent| omitted | Every session, everywhere — full disk walk plus every live session.                                                |
| absent| `true`  | Every live session, all cwds. The cheap path the watch picker uses.                                                |
| absent| `false` | Every cold session, all cwds. The historical-browser query.                                                        |

Request the TUI sends for the watch picker:

```json
{"type":"agent.list_sessions","id":"req_10","live":true}
```

Reply (one row per live session):

```json
{
  "type":"agent.sessions","id":"req_10",
  "sessions":[
    {
      "session_id":"5a01...",
      "backend":"claude",
      "attached":true,
      "cwd":"/home/u/proj",
      "model":"claude-sonnet-4-6",
      "title":"refactor utils.py",
      "started_at_ms":1745000000000,
      "last_active_at_ms":1745000123000,
      "owner_pid":12345,
      "last_seq":47,
      "turn_active":false
    }
  ]
}
```

The reply omits `cwd` at the top level (the request had no cwd
filter). Each row is a `SessionSummary`; the spec extended that
shape with seven new live-only optional fields:

- `cwd` — working directory the session is rooted at.
- `model` — last `agent.system_init.model` observed.
- `title` — first 80 chars of the first observed user message,
  whitespace-collapsed. Absent for sessions that never drove a turn.
- `started_at_ms` — wall-clock when the session was first registered.
- `last_active_at_ms` — wall-clock of the most recent `agent.result`
  (or `started_at_ms` if no turn has completed).
- `owner_pid` — `SO_PEERCRED` PID of the attached owner; absent when
  detached or when the kernel/platform doesn't expose peer creds.
  Matches `session_taken.by_peer_pid` semantics.
- `last_seq` — highest seq the session has produced. Same value the
  daemon carries on `agent.opened.last_seq` and
  `agent.watching.last_seq`.
- `turn_active` — true iff the session is between `agent.user` send
  and `agent.result` receive.

Disk-only rows carry `mtime_ms` / `size` / `preview` from the
original shape. When the request had no `cwd` filter (an all-cwds
query) the disk row additionally carries `cwd` and `model` extracted
from the transcript head, so the row is self-describing in the
absence of a top-level cwd echo. Sort order is `last_active_at_ms`
(preferred) falling back to `mtime_ms` (disk lag).

JSON Schemas:
[`inbound/agent.list_sessions.json`](https://github.com/blemees/blemees-daemon/blob/main/blemees/schemas/inbound/agent.list_sessions.json),
[`outbound/agent.sessions.json`](https://github.com/blemees/blemees-daemon/blob/main/blemees/schemas/outbound/agent.sessions.json),
and the extended `SessionSummary` `$def` in
[`_common.json`](https://github.com/blemees/blemees-daemon/blob/main/blemees/schemas/_common.json).

### 16.2 `agent.session_closed` (watcher-side)

New outbound frame. Emitted to every watcher of a session (not the
closer) immediately before the daemon unhooks their writers.

```json
{"type":"agent.session_closed","session_id":"5a01...","reason":"owner_closed"}
```

`reason` is forward-extensible; daemon 0.9.0 emits only
`"owner_closed"` (the explicit `agent.close` path). Future codes
(`idle_reaped`, `backend_crashed`) are reserved.

The closer does **not** receive `session_closed` — it gets the
`closed` ack to its own request. Owners and watchers thus get
distinct, non-overlapping signals; this lets the TUI handle the two
cases without trying to disambiguate.

JSON Schema:
[`outbound/agent.session_closed.json`](https://github.com/blemees/blemees-daemon/blob/main/blemees/schemas/outbound/agent.session_closed.json).

### 16.3 Rename: `blemees` → `blemeesctl` (clean break)

Daemon 0.9.0's `pyproject.toml`:

```toml
[project.scripts]
blemees-agentd = "blemees.__main__:main"
blemeesctl = "blemees.cli:main"
# `blemees` is intentionally NOT registered. From 0.9.0 on it
# belongs to the chat TUI shipped by blemees-tui.
```

**No deprecation alias.** The previous plan kept `blemees` as a
shim that warned to stderr and delegated to `main`, removed in 0.10.0.
That was scrapped because any alias would have collided with this
package's claim on the name — pip installs would have left
`blemees` owned by whichever wheel was installed last, with confusing
behavior either way.

Migration story for users:

- Daemon-only installs: typing `blemees` after upgrading to 0.9.0
  yields "command not found". Users retrain to `blemeesctl`. The
  daemon README has a prominent note at §0.
- Both daemon + TUI installed: `blemees` is the chat TUI. There is
  no ambiguity, no race condition, no deprecation warning to ignore.

Daemon 0.9.0's REPL prompt now reads `blemeesctl> `; the `--version`
banner reports `blemeesctl 0.9.0`; the hello frame's `client` field
identifies as `blemeesctl/<version>`.

---

## 17. Testing

### 17.1 Unit tests

- **Reducer** — exhaustive coverage of every `agent.*` and
  per-session `blemees-agentd.*` type. Property tests for sequence
  invariants (every turn ends with `agent.result`; `seq` strictly
  monotone).
- **Connection layer** — replay path, gap path, takeover path,
  watcher subscribe path, reconnect path. Use a fake `BlemeesClient`
  emitting scripted frame sequences.
- **Persistence** — round-trip `sessions.json` / `history.json`;
  schema migration scaffold for future bumps.
- **Config** — TOML parsing, override precedence, validation errors.

### 17.2 Snapshot tests

Textual's `Pilot` harness drives keystrokes; snapshot final terminal
output for:

- Empty state
- Mid-stream turn
- Tool block expanded / collapsed
- Error states (auth_failed, backend_crashed, replay_gap)
- Watch mode banner
- Reconnecting banner
- Event log overlay

### 17.3 End-to-end

Reuse `blemees-daemon/tests` mock backend fixtures. Spawn a real
`blemees-agentd` against `mock-claude` / `mock-codex` stubs, drive it
through TUI keystrokes, assert observable output.

### 17.4 Manual matrix (release gate)

- Live `claude` + live TUI: open, send, interrupt, close, delete,
  reconnect.
- Live `codex` + live TUI: same.
- Watch mode against a session driven by `blemeesctl` from another
  terminal.
- SSH-forwarded socket (TUI on laptop, daemon on remote).
- Two TUIs against one daemon: session takeover; watcher lifecycle.

---

## 18. Repository layout

```
blemees-tui/
├── README.md                  # short — install, screenshot, points to docs/SPEC.md
├── LICENSE                    # MIT
├── pyproject.toml
├── docs/
│   └── SPEC.md                # this document
├── blemees_tui/
│   ├── __init__.py
│   ├── __main__.py            # python -m blemees_tui
│   ├── app.py                 # Textual App subclass
│   ├── connection.py          # BlemeesClient wrapper + reducer pump
│   ├── state.py               # AppState, SessionStore, SessionState
│   ├── reducer.py             # pure agent.*/blemees-agentd.* → SessionState
│   ├── persistence.py         # sessions.json, history.json, log
│   ├── config.py              # TOML config loader
│   ├── widgets/
│   │   ├── __init__.py
│   │   ├── sidebar.py
│   │   ├── chat_pane.py
│   │   ├── composer.py
│   │   ├── footer.py
│   │   ├── event_log.py
│   │   ├── debug_pane.py
│   │   └── modals/
│   │       ├── __init__.py
│   │       ├── new_session.py
│   │       ├── attach.py
│   │       └── help.py
│   └── styles/
│       ├── dark.tcss
│       └── light.tcss
└── tests/
    ├── conftest.py
    ├── test_reducer.py
    ├── test_connection.py
    ├── test_persistence.py
    ├── test_config.py
    └── test_app_pilot.py
```

---

## 19. v0.1 scope summary

**Ship:**

- Multiplexed sessions, single connection, reconnect with replay.
- Markdown transcript with deltas, tool blocks, reasoning toggle.
- New / interrupt / close / delete / rename.
- Watch mode + take-ownership. The two daemon-side bits this depends
  on (§16.1, §16.2) landed in daemon 0.9.0; there is no protocol
  work blocking v0.1.
- Footer chip + event log + persistent log + debug pane.
- Inline error rendering.
- Multiline composer, history, slash commands.
- Configurable theme, keybindings, defaults.
- PyPI + uv tool + pipx + Homebrew.

**Defer to v0.2:**

- Historical session browser (`list_sessions <cwd>` cold sessions).
- Multimodal input (Claude images).
- Side-by-side compare.
- Profiles, per-cwd auto-defaults.
- Full-text search across sessions.
- JSONL transcript export.
- Plugins, custom themes.

---

## 20. Release sequencing

1. **Daemon 0.9.0** ✅ — extends `agent.list_sessions` (composable
   `cwd` + `live` filters, richer `SessionSummary`), adds
   `agent.session_closed`, renames console_script to `blemeesctl`
   (no deprecation alias). Ready to ship to PyPI + Homebrew.
2. **`blemees-tui` v0.1** — targets `blemees>=0.9.0`. Ships to PyPI,
   `uv tool`, `pipx`. Homebrew formula added to `blemees/homebrew-tap`.

After step 2, `pip install blemees-tui` cleanly installs the chat
TUI as `blemees`; `pip install blemees` installs the daemon with
`blemees-agentd` and `blemeesctl`. The two console_script namespaces no
longer overlap, so installing both is a no-op for the `blemees`
binding (TUI owns it, daemon doesn't claim it).

There is no daemon 0.10.0 follow-up needed for the rename — the
clean break landed in 0.9.0.
