# blemees-tui — implementation plan

**Companion to:** [`SPEC.md`](SPEC.md). Each milestone is independently
shippable as a `0.1.x` and unlocks the next; section refs (§) point at
`SPEC.md`.

---

## M1 — Owned-session happy path (the demo)

The path you'll actually dogfood: open a Claude session, drive a turn,
see streaming, close, restart, verify history.

1. **Reducer fills.** `agent.system_init` → `started_at_ms`;
   `blemeesd.session_info_reply` → `context_tokens`. Drop the
   `options["_notices"]` rate_limits hack — promote to `AppState`.
2. **App plumbing.** Active-session switching (`1`..`9`, `Ctrl+Tab`);
   `turn_active` disables composer (§7.2); composer history recall
   (`Up`/`Down` in empty input, §10).
3. **ChatPane render pass.** Replace `Static` lines with Markdown;
   update incrementally on each frame instead of rebuilding; result
   divider; live deltas.
4. **History wiring.** On close/delete, append to `history.json` and
   render under "─ history ─" in the sidebar.

**Acceptance:** open Claude session, drive a turn, see streaming text +
tool block, close it, reopen the TUI, history pane shows it.

---

## M2 — Watch mode end-to-end (§8)

The differentiator vs `blemeesctl`. Cross-cutting (connection + state +
UI), so write the reconnect-ordering test first.

5. **Attach modal v2.** Call `connection.list_sessions(live=True)`,
   render the picker table from §8.1.
6. **Watch banner + button row.** ChatPane header when `mode=watching`;
   replace composer with `[Take ownership]` `[Stop watching]`.
7. **Take-ownership flow.** `blemeesd.open(resume=True)` → flip `mode`,
   swap UI, persist.
8. **`session_closed` watcher banner** + `Move to history` action.

**Acceptance:** drive a session from `blemeesctl`, watch it from the
TUI, take ownership, send a turn from the TUI.

---

## M3 — Errors, observability, recovery (§14, §15)

Hardening pass. Everything that should already be glued in but renders
silently today.

9. **Inline error bubbles** for session-scoped errors (auth_failed,
   backend_crashed); replay_gap banner.
10. **Footer chip** wires `! N errors`, `↺ rate`, ctx tokens.
11. **EventLog filter chips** + `[/]` filter + `[s]` save.
12. **Reconnect banner** ("reconnecting attempt N, next in Ms") — today
    it only goes to the log.
13. **Slow-consumer / oversize / daemon_shutdown** explanatory banners.

---

## M4 — Modal depth + slash commands (§7.1, §10)

Paper-cuts. Won't block dogfooding but needed for the v0.1 contract.

14. **NewSession Advanced section** — per-backend options table (Claude:
    `permission_mode`, `tools`, `system_prompt`, `effort`, etc.; Codex:
    `sandbox`, `approval-policy`, raw `config`).
15. **TUI commands** — vim-style ``:`` prefix (`:new`, `:close`,
    `:delete`, `:interrupt`, `:rename`, `:cwd`, `:model`, `:watch`,
    `:help`, `:q`) with Tab-complete. ``/`` is reserved for the active
    backend so Claude Code skills / Codex slash commands pass through.
16. **Ctrl+S transcript export** to `$XDG_STATE_HOME/blemees-tui/transcripts/`.

---

## M5 — Tests + release (§17, §20)

The release gate.

17. **Reducer property tests** — seq-monotone, every turn ends with
    `agent.result`, tool_use→tool_result pairing across reordering.
18. **Pilot snapshot tests** for the eight scenes in §17.2 (empty,
    mid-stream, tool expanded/collapsed, auth_failed, backend_crashed,
    replay_gap, watch banner, reconnecting, event log).
19. **End-to-end with mock-claude / mock-codex** — reuse the daemon
    fixtures from `blemees-daemon/tests`.
20. **Release prep** — Homebrew formula in `homebrew-tap`, PyPI metadata
    polish, screenshot in README.

---

## M6 — Claude Code parity sprint

Brings the TUI from "chat client that drives Claude Code" to "feels like
Claude Code, with watch mode bolted on for free." Items are independently
shippable; sequence is by user-visible value.

21. **Built-in command refresh on connect.** `discover.BUILTIN_CC_COMMANDS`
    is a static seven-entry list — Claude Code ships ~50 + bundled skills
    + plugin commands + MCP prompts, and the set churns. Probe `/help`
    once per (backend, version) on first connect, parse, cache to
    `$XDG_STATE_HOME/blemees-tui/help-<backend>-<version>.json`. Use
    cached entries for the `/`-completion popup.
22. **`@`-mention file completion.** Composer doesn't recognise `@` today
    — typing `@src/auth.ts` is a literal string. Add a third pool to
    `widgets/completion.py` that walks the active session's `cwd` (and
    any `--add-dir`-equivalent) ranked by recency. Tag entries `file` /
    `dir`. The Claude Code reflex most users miss in v0.1.
23. **Permission-mode badge + `Shift+Tab` cycle.** Today the active
    permission mode is invisible after session creation. Render a
    coloured chip in the chat header (`⏵⏵ accept edits`, `⏸ plan`, etc.)
    and bind `Shift+Tab` to cycle `default → acceptEdits → plan` (skip
    `bypassPermissions` unless explicitly opted in via flag). Confirm
    with the daemon team whether mid-session re-config is supported; if
    not, ship the badge anyway and document the cosmetic-only path.
24. **Resume / history picker for closed sessions.** `Ctrl+T` only lists
    *live* sessions. Add a Resume tab to `widgets/modals/attach.py`
    backed by `list_sessions(cwd=…, live=False)`; on submit issue
    `blemeesd.open{resume:true, last_seen_seq:0}` to replay from disk.
25. **Mid-session `:model` and `:effort`.** `:model` is documented in
    `commands.py` as cosmetic-only; `:effort` doesn't exist. Either wire
    them to a daemon re-config verb (preferred) or label them as UI
    relabel + warn. Add Tab-complete pools: `sonnet`, `opus`, `haiku`,
    `sonnet[1m]`, `opus[1m]`, `opusplan` for model; `low/medium/high/
    xhigh/max` for effort.
26. **Image paste in composer.** Spec §3 defers multimodal but Claude
    Code-native users paste screenshots constantly. Catch `Ctrl+V` of
    image data, write to `$XDG_CACHE_HOME/blemees-tui/pastes/`, insert
    `[Image #N]` chip, send `agent.user` with multipart `content`. Stub
    OK for v0.1.x; full drag-and-drop can wait.

**Acceptance:** a Claude Code power user can drive blemees-tui for a day
without missing `@`-completion, mode-cycling, model-switching, or
resume-by-name. Image paste works for screenshots.

---

## M7 — Polish & long-tail (post-v0.1)

Stretch goals from the parity audit; pick from the list as time allows.

27. **Plan-mode banner** when `permission_mode=plan` is active (reuse
    `_sync_banner` with a new state); `Ctrl+G` opens last assistant
    message in `$EDITOR`.
28. **`:cost` / `:usage` / `:context` overlay** rendering the data we
    already compute (`Usage`, `cumulative_usage`, ctx tokens) plus a
    by-category breakdown from `session_info_reply`.
29. **Reverse history search (`Ctrl+R`)** across all sessions in the
    current project, not just the active one.
30. **`!`-prefix shell mode** in the composer (forward verbatim — let
    Claude Code execute, don't reinvent local subprocess plumbing).
31. **Auto-memory icon** in the transcript when `memory_write` /
    `memory_read` tool calls fire (detect by name in the reducer).
32. **Output-style** field in the new-session modal (`outputStyle` in
    Claude options).
33. **Hook-event filter chip** in the event log.
34. **PR-status footer badge** behind a config flag (`gh pr status`
    poll).
35. **`/recap` overlay** with a one-liner summary of the last 3 turns
    when the user returns after >3 min idle.
36. **Keybindings TOML override** — spec §11 promises this; `app.BINDINGS`
    is hardcoded today.
37. **Statusline** support reading the user's `~/.claude/settings.json`
    `statusLine` setting (avoid duplicate config).
38. **`Ctrl+G` external-editor escape** for the composer.
39. **Fix `Ctrl+T` collision** — Claude Code uses it for the task list,
    we use it for Attach. Move Attach to `Ctrl+Shift+A` if/when we add
    task-list rendering.
40. **Sub-agent visual nesting** — indent sub-agent turns under their
    parent tool call so the reader can tell which agent did what.
41. **`argument-hint` in skill completion popup** — already parsed in
    `discover.py`, just not displayed.

---

## Conscious non-goals

The TUI deliberately does *not* implement these — different surface, or
the user already configures them outside any session:

- IDE / Web / Desktop / Slack / Telegram / Discord / iMessage /
  Channels / GitHub Actions / Routines / Chrome / Dispatch.
- `claude auth login`, `claude mcp`, plugin marketplace browser,
  `/permissions` editor, hook/MCP/agent definition UIs — point users at
  the upstream CLI's config flows.
- `--worktree`, `--tmux`, `--teleport`, `--rc`, agent teams (multi-pane).
- `-p` / `--print` / `--bare` headless — meaningless for an interactive
  frontend.
- `--fork-session`, `--from-pr`, `--init-only`, `--maintenance` — niche
  CLI orchestration.

---

## Risks worth tracking

- **Streaming Markdown perf in Textual.** The `Markdown` widget rebuilds
  on every change. Land naïvely in M1 and measure; if it bites, drop to
  a line-diffing custom widget.
- **Reducer mutation vs. time-travel.** The reducer mutates in place.
  Fine for now; if M3 wants frame-replay in the debug pane, swap to
  immutable. Don't preempt.
- **Watch-mode reconnect ordering (§8.7).** The only place where
  on-frame error handling races with tracked-session bookkeeping. Test
  first in M2 before the UI work.
