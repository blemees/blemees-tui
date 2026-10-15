# End-to-end smoke

The unit and Pilot tests mock the daemon socket. This page covers the one
path they don't: the real TUI, against a live `blemees-agentd`, driving a
real ACP agent (#13).

## Automated: `tests/test_e2e_smoke.py`

Opt-in, like blemees-agent's e2e matrix. It never runs in CI.

```sh
BLEMEES_E2E=1 pytest tests/test_e2e_smoke.py -v
```

Each run starts its own `blemees-agentd` on a socket in `/tmp`, with one
profile, `smoke`, that runs `claude-agent-acp` in mode `default`. Then it
drives the TUI through Textual's `Pilot`:

1. **Connect.** `hello_ack` arrives.
2. **Open under a profile.** `Ctrl+N` → the new-session modal lists the
   `smoke` profile's agent → **Open** → `session.opened`.
3. **Prompt + stream.** The test types a prompt and presses Enter. It
   checks that `session.update` frames stream in, that `session.result`
   ends the turn, and that one turn with the reply is shown.
4. **Reattach as a viewer.** A second TUI starts with empty local state.
   `Ctrl+T` → the session is in the live list → **Watch (viewer)**. The
   replay rebuilds the transcript and the composer is disabled.
5. **Inline permission.** A new session asks the agent to write a file.
   The permission card appears → click an allow option → the turn ends
   and the file exists.

Requirements: `blemees-agentd` (≥ 0.11.0) and `claude-agent-acp` on
`PATH`, and you are logged in to Claude. If the agent never answers or
reports an error (for example `auth_required`), the test is skipped, not
failed. If the turn ends without a permission request (the tool was
pre-approved), step 5 is skipped.

| Variable | Default | Purpose |
|---|---|---|
| `BLEMEES_E2E` | unset | Set to `1` to run the smoke. |
| `BLEMEES_E2E_AGENTD` | `blemees-agentd` on `PATH` | Daemon binary. |
| `BLEMEES_E2E_AGENT` | `claude-agent-acp` | ACP agent command line (shell-split). |

To check the harness without tokens, point it at blemees-agent's fake
agent. The fake agent writes no file, so the file check is skipped:

```sh
BLEMEES_E2E=1 \
BLEMEES_E2E_AGENT="$(which python) ../blemees-agent/tests/blemees_agent/fake_acp.py" \
pytest tests/test_e2e_smoke.py -v
```

The daemon log is in the run's `/tmp/bt-e2e-*` directory while the
tests run.

## Manual checklist

Do this before a release, or when you change the connection, the
reducer, or the chat pane. Use a scratch directory as the session cwd.

**Setup**

- [ ] Start a daemon with a profile for the agent:
      `blemees-agentd --socket /tmp/bt-smoke.sock --config smoke.toml`,
      where `smoke.toml` has `[profiles.smoke]` and
      `agent_command = "claude-agent-acp"`.
- [ ] Start the TUI: `blemees --socket /tmp/bt-smoke.sock --profile smoke`.
- [ ] The footer shows the daemon as connected. No reconnect banner.

**Open + prompt**

- [ ] `Ctrl+N`: the modal shows `profile: smoke` and lists its agent(s).
      Set cwd to the scratch dir and press **Open**.
- [ ] The session appears in the sidebar and becomes active.
- [ ] Send `Reply with exactly the word: pong`. The text streams in. The
      turn-status bar clears when the turn ends.
- [ ] Send a second message while a turn runs. It is queued, then sent
      when the turn ends.
- [ ] `Ctrl+C` while a long turn runs (`Count slowly from 1 to 100`)
      stops it.

**Permission**

- [ ] Ask the agent to create a file in the cwd. A permission card
      appears inline, with the agent's options and **Cancel**.
- [ ] Click an allow option. The card goes away, the tool runs, and the
      file exists.
- [ ] Do it again and click a reject option. The card goes away and the
      agent reports that it did not run the tool.

**Reattach**

- [ ] Quit the TUI (`q`) and start it again. The session is restored and
      the transcript paints at once (snapshot), with no duplicate turns.
- [ ] In a second terminal, start another TUI on the same socket.
      `Ctrl+T` lists the session. Select it → **Watch (viewer)**.
- [ ] The viewer shows the transcript, the "watching" banner, and a
      disabled composer.
- [ ] Prompt from the owner TUI. The viewer streams the same turn.
- [ ] In the viewer, **Take ownership**. The composer is enabled, and
      the first TUI shows the "Detached" banner with **Reclaim**.

**Daemon restart**

- [ ] Stop the daemon while the TUI runs. The reconnect banner appears.
      Type a message: it is queued, and the app does not crash.
- [ ] Start the daemon again. The TUI reconnects and sends the queued
      message.
