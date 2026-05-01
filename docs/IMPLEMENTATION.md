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
