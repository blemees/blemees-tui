"""Markdown transcript export (spec §13).

Pure: ``render(session) → str``. The app writes the result to
``$XDG_STATE_HOME/blemees-tui/transcripts/<title-slug>-<short-id>.md``.
"""

from __future__ import annotations

import json

from .state import SessionState, TextBlock, ThinkingBlock, ToolUseBlock, Turn


def render(session: SessionState) -> str:
    lines: list[str] = []
    title = session.title or session.session_id[:8]
    lines.append(f"# {title}")
    lines.append("")
    lines.append(f"- **session_id**: `{session.session_id}`")
    if session.backend:
        lines.append(f"- **backend**: {session.backend}")
    if session.model:
        lines.append(f"- **model**: {session.model}")
    if session.cwd:
        lines.append(f"- **cwd**: `{session.cwd}`")
    if session.started_at_ms:
        lines.append(f"- **started_at_ms**: {session.started_at_ms}")
    lines.append("")
    lines.append("---")
    lines.append("")

    for idx, turn in enumerate(session.turns, start=1):
        lines.append(f"## Turn {idx}")
        lines.append("")
        if turn.user_text:
            lines.append("**user**")
            lines.append("")
            lines.append("> " + turn.user_text.replace("\n", "\n> "))
            lines.append("")
        for block in turn.blocks:
            if isinstance(block, ThinkingBlock) and block.text:
                lines.append("**reasoning**")
                lines.append("")
                lines.append("```")
                lines.append(block.text)
                lines.append("```")
                lines.append("")
            elif isinstance(block, TextBlock) and block.text:
                lines.append("**assistant**")
                lines.append("")
                lines.append(block.text)
                lines.append("")
            elif isinstance(block, ToolUseBlock):
                lines.append(f"**tool_use** · `{block.name}`")
                lines.append("")
                lines.append("```json")
                lines.append(json.dumps(block.input, indent=2, ensure_ascii=False))
                lines.append("```")
                if block.result_text:
                    label = "tool_result (error)" if block.is_error else "tool_result"
                    lines.append(f"_{label}_")
                    lines.append("")
                    lines.append("```")
                    lines.append(block.result_text)
                    lines.append("```")
                lines.append("")
        if turn.duration_ms is not None:
            usage_bits = [f"duration={turn.duration_ms / 1000:.2f}s"]
            usage_bits.append(f"in={turn.usage.input_tokens}")
            usage_bits.append(f"out={turn.usage.output_tokens}")
            if turn.result_subtype:
                usage_bits.append(f"subtype={turn.result_subtype}")
            lines.append(f"_{', '.join(usage_bits)}_")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def render_turn(turn: Turn) -> str:
    """Single-turn render — used by tests and the in-memory preview."""
    sess = SessionState(session_id="preview", turns=[turn])
    out = render(sess)
    # Strip the header — caller wanted the turn body.
    head, _, body = out.partition("## Turn 1")
    return ("## Turn 1" + body).rstrip() + "\n" if body else out
