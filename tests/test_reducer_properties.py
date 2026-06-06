"""Reducer invariants — blemees/3 (#1/#2).

Light-touch property checks (no hypothesis dep): generated session.update
sequences exercise the reducer's hot path. Asserts the contract:

* Every turn ends with ``session.result`` (locked == True).
* ``last_seq`` is strictly monotone.
* ``tool_call`` / ``tool_call_update`` pair by tool_call_id across reorderings.
* ``Usage.merge`` is associative.
"""

from __future__ import annotations

import itertools
import random
import string

from blemees_tui.reducer import apply, apply_user_prompt
from blemees_tui.state import SessionState, ToolUseBlock, Usage


def _update(sess: SessionState, seq: int, update: dict) -> None:
    apply(sess, {"type": "session.update", "session_id": "prop", "seq": seq, "update": update})


def _seq_counter(start=0):
    n = start
    while True:
        n += 1
        yield n


def _build_session(rng: random.Random, num_turns: int) -> SessionState:
    sess = SessionState(session_id="prop")
    seq = _seq_counter()

    for turn_idx in range(num_turns):
        apply_user_prompt(sess, f"q{turn_idx}")
        for _ in range(rng.randint(1, 5)):
            text = "".join(rng.choices(string.ascii_lowercase, k=rng.randint(1, 8)))
            _update(
                sess,
                next(seq),
                {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text}},
            )
        # Maybe a tool round-trip.
        if rng.random() < 0.5:
            tu_id = f"tu_{turn_idx}"
            _update(
                sess,
                next(seq),
                {"sessionUpdate": "tool_call", "toolCallId": tu_id, "title": "Read"},
            )
            _update(
                sess,
                next(seq),
                {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": tu_id,
                    "status": "completed",
                    "content": [{"type": "content", "content": {"type": "text", "text": "ok"}}],
                },
            )
        apply(
            sess,
            {
                "type": "session.result",
                "session_id": "prop",
                "seq": next(seq),
                "stop_reason": "end_turn",
                "usage": {
                    "inputTokens": rng.randint(0, 100),
                    "outputTokens": rng.randint(0, 200),
                },
            },
        )
    return sess


def test_every_completed_turn_is_locked():
    rng = random.Random(0xBEEF)
    sess = _build_session(rng, num_turns=8)
    assert len(sess.turns) == 8
    for t in sess.turns:
        assert t.locked, "every turn driven through session.result must end locked"
        assert t.result_subtype == "end_turn"


def test_last_seq_strictly_monotone():
    sess = SessionState(session_id="m")
    seq = 0
    chunk = {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "x"}}
    for _ in range(50):
        seq += 1
        apply(sess, {"type": "session.update", "session_id": "m", "seq": seq, "update": chunk})
        assert sess.last_seq == seq
    # Out-of-order seqs should not regress.
    apply(sess, {"type": "session.update", "session_id": "m", "seq": 10, "update": chunk})
    assert sess.last_seq == 50


def test_tool_calls_pair_with_updates_across_orderings():
    """Even when several tool_calls interleave, each tool_call_update lands on
    its matching block by tool_call_id."""
    sess = SessionState(session_id="pair")
    apply_user_prompt(sess, "go")
    ids = ["a", "b", "c"]
    for i, tu in enumerate(ids, start=2):
        _update(sess, i, {"sessionUpdate": "tool_call", "toolCallId": tu, "title": "T"})
    # Updates arrive in reverse order.
    for i, tu in enumerate(reversed(ids), start=10):
        _update(
            sess,
            i,
            {
                "sessionUpdate": "tool_call_update",
                "toolCallId": tu,
                "status": "completed",
                "content": [{"type": "content", "content": {"type": "text", "text": f"r-{tu}"}}],
            },
        )
    by_id = {b.tool_use_id: b for b in sess.turns[-1].blocks if isinstance(b, ToolUseBlock)}
    for tu in ids:
        assert by_id[tu].result_text == f"r-{tu}", f"{tu} got {by_id[tu].result_text}"
        assert by_id[tu].status == "completed"


def test_usage_merge_is_associative():
    """`(a + b) + c == a + (b + c)`."""
    samples = [
        Usage(input_tokens=1, output_tokens=2),
        Usage(input_tokens=3, output_tokens=5, reasoning_output_tokens=1),
        Usage(cache_creation_input_tokens=4, cache_read_input_tokens=7),
    ]
    for a, b, c in itertools.permutations(samples, 3):
        left = a.merge(b).merge(c)
        right = a.merge(b.merge(c))
        assert left == right, (a, b, c)
