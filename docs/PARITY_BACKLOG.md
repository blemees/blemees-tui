# Claude Code parity — refinement backlog

Captured from the parity-audit conversation. Each "selected" item still
needs design decisions before implementation starts; the open questions
listed are what blocked us at conversation time.

The full feature audit (the 80-feature inventory + status table) lives
inline in chat history; if you need it again, re-run a research pass
against `docs.claude.com/en/docs/claude-code/`. This file only tracks
what we decided to act on (or not).

---

## Selected for refinement

The user's prioritised shortlist. Each item has an opening implementation
take + open questions that need answers before building.

### 1. `@`-mention file completion

- **Default approach**: extend `widgets/completion.py` with a third pool
  triggered by `@`. Walk the active session's `cwd` recursively, honour
  `.gitignore`, rank by recency.
- **Open questions**:
  - Should completion pull from any `--add-dir`-equivalents, or just the
    primary `cwd`?
  - Insert the file *path* (text only — agent reads it) or attempt
    inline content insertion? Default: text only, matching Claude Code.

### 2. Permission mode — *with user-specified modifications*

- **Default approach**: render a coloured chip in the chat header
  showing the active mode; `Shift+Tab` cycles
  `default → acceptEdits → plan` (skip `bypassPermissions` unless
  explicitly opted in).
- **Open questions**:
  - User flagged "with modifications" — what specific changes from
    Claude Code's behaviour? Capture before designing.
  - Does the daemon support mid-session permission-mode change, or is
    this badge cosmetic-only until session restart?

### 3. Resume closed sessions

- **Default approach**: add a "Resume" tab to
  `widgets/modals/attach.py` populated from `history.json` and
  `connection.list_sessions(live=False)`. Submit issues
  `blemeesd.open{resume:true, last_seen_seq:0}`.
- **Open questions**:
  - Fuzzy search vs. plain sortable list? Default to plain sortable by
    recency unless user wants fuzzy.
  - Show transcript preview on hover/select?

### 4. Shell mode (`!`-prefix in composer)

- **Two implementation paths**:
  - **(a) Local intercept** — TUI runs subprocess, posts output as a
    synthetic `agent.user` message. Matches Claude Code's actual
    behaviour.
  - **(b) Passthrough** — forward `!cmd` verbatim, let Claude Code
    execute via Bash tool.
- **Open question**: which path? Default lean: (b) for safety and
  protocol simplicity.

### 5. Mid-session `:model` / `:effort` change

- **Default approach**: parse `:model <alias>` / `:effort <level>` in
  `commands.py`, send to daemon. Tab-complete pools:
  - `model`: `sonnet` `opus` `haiku` `sonnet[1m]` `opus[1m]` `opusplan`
  - `effort`: `low` `medium` `high` `xhigh` `max`
- **Open questions**:
  - Daemon API: new verb (e.g. `blemeesd.reconfigure`) or re-issue
    `blemeesd.open` with new options? Need to inspect daemon protocol
    docs / source.
  - User confirmed daemon supports mid-session model changes — verify
    the wire shape before designing.

### 6. Context window usage % in statusline

- **Default approach**: in `widgets/footer.py`, render `ctx N% (Tk/Mk)`
  where N is `context_tokens / context_window`. Colour-graded:
  green < 50%, yellow 50–80%, red > 80%.
- **Open question**: above thresholds, also flash a warning chip /
  notification, or rely on the user noticing the colour?

### 7. Statusline with more info

- Overlaps with #6. **Default additions**: turn count for active
  session, model alias, current permission-mode chip, cumulative session
  cost (computable from `cumulative_usage`).
- **Open question**: anything else the user wants surfaced (git branch,
  uptime, daemon version, …)?

### 8. Subagent visual nesting + attaching to subagent sessions

- **Two parts**:
  - **Visual**: indent sub-agent turns under their parent tool call in
    `widgets/chat_pane.py`. Show subagent name + status on the parent
    block.
  - **Attaching**: surface subagent sessions in the sidebar as nested
    rows under the parent, switchable like any other session.
- **Open question**: does the daemon expose subagent sessions as
  separately-addressable `session_id`s today, or are they currently
  collapsed into the parent's frame stream? If the latter, this needs
  daemon-side work first.

---

## New architectural ideas (raised by user)

### Connecting to agents in Docker containers (via socket file)

- **No TUI changes needed for the basic case** — `--socket /path` and
  `BLEMEESD_SOCKET=` env already exist. Compose patterns:
  - Daemon in container, socket bind-mounted out:
    `-v /tmp/blemeesd.sock:/host/sock`
  - Daemon on host, socket mounted into container.
  - Remote host: `ssh -L /local/sock:/container/sock user@host`.
- **Possible TUI work**: `:connect <path>` command to swap socket
  without restarting. Useful for users who switch between local and
  containerised daemons mid-session.
- **Documentation work**: a short "running blemeesd in Docker" section
  in the README covering the three patterns above.

### More useful left-hand panels / plugin-rendered panels

- **Three architectural options**:
  - **(a) Tabbed sidebar** — switch between Sessions / Files / Tasks /
    Skills / Status panels via `Alt+1..N` or click. Lowest risk.
  - **(b) Stacked panels** — sessions on top, configurable second pane
    below (file tree, recent edits, etc.).
  - **(c) Plugin slot** — Python-importable interface; users drop a
    Textual widget into `~/.config/blemees-tui/panels/foo.py` and it
    appears. Highest power, highest API-stability commitment.
- **Open question**: which direction? Default lean: (a) tabbed sidebar
  first, (c) plugin slot only after the tabbed sidebar's API has
  stabilised.

---

## Considered but deferred / excluded

Captured here so we don't re-litigate them in the next planning pass.

### Excluded outright

- **Plan-mode banner / `Shift+Tab` for plan / `Ctrl+G` plan-edit**
  → user does not use plan mode.
- **Image paste in composer / multimodal input**
  → user does not paste screenshots; v0.1 multimodal stays deferred.
- **`--worktree`, `--tmux`, `--teleport`, `--rc`, agent teams**
  → out of scope for a single-window TUI.
- **`-p` / `--print` / `--bare` headless modes**
  → meaningless for an interactive frontend.
- **`--fork-session`, `--from-pr`, `--init-only`, `--maintenance`**
  → niche CLI orchestration, not chat-UX.
- **IDE / Web / Desktop / Slack / Telegram / Discord / iMessage /
  Channels / GitHub Actions / Routines / Chrome / Dispatch**
  → different surfaces; the TUI competes with none of them.
- **`claude auth login` / `claude mcp` / plugin marketplace browser /
  `/permissions` editor / hook + agent definition UIs**
  → users already configure these via the upstream CLI; the TUI should
  point at those, not reinvent.

### Deferred (might revisit)

- **`:cost` / `:usage` / `:context` overlay** — already exposed in
  footer (`ctx Tk/Mk`); a full modal felt like overkill. Reconsider if
  cost-tracking becomes a daily concern.
- **Reverse history search across projects (`Ctrl+R`)** — per-session
  Up/Down recall covers ~95% of need. Reconsider if cross-session
  history feels like a frequent miss.
- **PR-status footer badge** — niche; `gh pr status` is one keystroke
  away. Reconsider only if PR-driven work dominates.
- **`/recap` auto-popup on idle** — watch mode + visible chat pane
  already solves the "what happened while I was away" problem better.
- **Custom statusline running a user command** (Claude Code's
  `statusLine` setting) — duplicates info our footer already shows;
  build only if specific request emerges.
- **`Ctrl+G` external-editor escape for composer** — Textual TextArea
  handles big paste / multi-line fine. Niche unless user is vim-pilled.
- **Vim editor mode in composer** — niche; deferred until a clear ask.
- **Auto-memory icon** for `memory_write` / `memory_read` tool calls
  — pattern detection is fragile; tool calls already render as
  `▸ memory_write(...)`. Reconsider if memory-management UX becomes a
  pain point.
- **Built-in command refresh via hidden one-shot session** — feels
  brittle. Better path: ask the daemon team for a
  `blemeesd.list_skills` / `list_commands` verb. Until then, the static
  list in `discover.BUILTIN_CC_COMMANDS` is fine.
- **`@server:resource` MCP resource completion** — daemon owns MCP
  config; users who set up MCP know their resource names.
- **Hook-event filter chip in event log** — free if/when the daemon
  forwards `hook.*` frames; until then nothing to filter.
- **Output-style picker in new-session modal** — single-field add when
  the daemon is ready to accept it. Trivial work; deferred only because
  no one's asked.
- **Keybindings TOML override loader** — spec §11 promises it;
  hardcoded today. Defer until a user explicitly wants to rebind.
- **Sub-agent argument-hint in skill completion popup** — already parsed
  in `discover.py`, just not displayed. Half-day work; deferred.
- **`Ctrl+T` collision fix** — Claude Code uses `Ctrl+T` for task list,
  we use it for Attach. Move Attach to `Ctrl+Shift+A` *if and when* we
  add a task-list panel.

---

## How to use this document

When picking the next milestone:

1. Pull an item from "Selected for refinement", answer its open
   questions with the user.
2. Promote the resulting design into `IMPLEMENTATION.md` as an M6/M7
   numbered task and start building.
3. If the item turns out infeasible (daemon support missing, etc.),
   move it to the "Deferred" list with a one-line reason so the next
   pass doesn't re-trip on it.
