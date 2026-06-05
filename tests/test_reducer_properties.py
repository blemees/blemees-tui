"""Reducer invariants (§17.1).

Light-touch property checks (no hypothesis dep): generated frame
sequences exercise the reducer's hot path. Asserts the contract:

* Every turn ends with ``agent.result`` (locked == True).
* ``last_seq`` is strictly monotone.
* ``tool_use`` / ``tool_result`` pair across reorderings.
* ``Usage.merge`` is associative.
"""

from __future__ import annotations

import itertools
import random
import string

import pytest

from blemees_tui.reducer import apply
from blemees_tui.state import SessionState, ToolUseBlock, Usage


def _seq_counter(start=0):
    n = start
    while True:
        n += 1
        yield n


def _build_session(rng: random.Random, num_turns: int) -> SessionState:
    sess = SessionState(session_id="prop")
    seq = _seq_counter()

    for turn_idx in range(num_turns):
        apply(
            sess,
            {
                "type": "agent.user",
                "session_id": "prop",
                "seq": next(seq),
                "message": {"role": "user", "content": f"q{turn_idx}"},
            },
        )
        # Random number of streaming deltas
        for _ in range(rng.randint(1, 5)):
            text = "".join(rng.choices(string.ascii_lowercase, k=rng.randint(1, 8)))
            apply(
                sess,
                {
                    "type": "agent.delta",
                    "session_id": "prop",
                    "seq": next(seq),
                    "kind": "text",
                    "text": text,
                },
            )
        # Maybe a tool round-trip
        if rng.random() < 0.5:
            tu_id = f"tu_{turn_idx}"
            apply(
                sess,
                {
                    "type": "agent.tool_use",
                    "session_id": "prop",
                    "seq": next(seq),
                    "tool_use_id": tu_id,
                    "name": "Read",
                    "input": {"path": f"/p/{turn_idx}"},
                },
            )
            apply(
                sess,
                {
                    "type": "agent.tool_result",
                    "session_id": "prop",
                    "seq": next(seq),
                    "tool_use_id": tu_id,
                    "output": "ok",
                },
            )
        apply(
            sess,
            {
                "type": "agent.result",
                "session_id": "prop",
                "seq": next(seq),
                "subtype": "success",
                "duration_ms": rng.randint(50, 5000),
                "usage": {
                    "input_tokens": rng.randint(0, 100),
                    "output_tokens": rng.randint(0, 200),
                },
            },
        )
    return sess


def test_every_completed_turn_is_locked():
    rng = random.Random(0xBEEF)
    sess = _build_session(rng, num_turns=8)
    for t in sess.turns:
        assert t.locked, "every turn driven through agent.result must end locked"
        assert t.result_subtype == "success"


def test_last_seq_strictly_monotone():
    sess = SessionState(session_id="m")
    seq = 0
    for _ in range(50):
        seq += 1
        apply(
            sess,
            {
                "type": "agent.delta",
                "session_id": "m",
                "seq": seq,
                "kind": "text",
                "text": "x",
            },
        )
        assert sess.last_seq == seq

    # Out-of-order seqs should not regress.
    apply(sess, {"type": "agent.delta", "session_id": "m", "seq": 10, "kind": "text", "text": "y"})
    assert sess.last_seq == 50


@pytest.mark.skip(reason="tool_use/tool_result pairing is the ACP tool vocabulary — lands in #2")
def test_tool_use_pairs_with_result_across_orderings():
    """Even when several tool_uses interleave, each tool_result lands on
    its matching block."""
    sess = SessionState(session_id="pair")
    apply(
        sess,
        {
            "type": "agent.user",
            "session_id": "pair",
            "seq": 1,
            "message": {"role": "user", "content": "go"},
        },
    )
    ids = ["a", "b", "c"]
    for i, tu in enumerate(ids, start=2):
        apply(
            sess,
            {
                "type": "agent.tool_use",
                "session_id": "pair",
                "seq": i,
                "tool_use_id": tu,
                "name": "T",
                "input": {"i": i},
            },
        )
    # Results arrive in reverse order.
    for i, tu in enumerate(reversed(ids), start=10):
        apply(
            sess,
            {
                "type": "agent.tool_result",
                "session_id": "pair",
                "seq": i,
                "tool_use_id": tu,
                "output": f"r-{tu}",
            },
        )
    by_id = {b.tool_use_id: b for b in sess.turns[-1].blocks if isinstance(b, ToolUseBlock)}
    for tu in ids:
        assert by_id[tu].result_text == f"r-{tu}", f"{tu} got {by_id[tu].result_text}"


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
