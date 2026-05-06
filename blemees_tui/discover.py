"""Discover Claude Code skills + slash commands available to the user.

Today the discovery is purely filesystem-based — we walk the standard
``~/.claude/skills/`` and ``~/.claude/plugins/<plugin>/skills/`` trees,
parse YAML frontmatter for ``name`` + ``description``, and merge the
result with a hardcoded set of Claude Code built-in slash commands.

A future pass can enrich this with parsed ``/context`` output (per
session: real MCP server list, active agents, current context-window
usage) and cache it on ``SessionState``.

Pure: no Textual / connection dependencies.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Suggestion:
    """One row in the completion popup."""

    label: str  # what gets inserted on accept (e.g. "/skill-name")
    description: str = ""  # short blurb shown in the right column
    source: str = ""  # "builtin" | "skill" | "tui" | "plugin:<name>"


# ---------------------------------------------------------------------------
# Built-in Claude Code slash commands
# ---------------------------------------------------------------------------


# Claude Code first-party slash commands that are actually available via
# ``claude -p`` (headless mode). Probed empirically against Claude Code
# 2.1.126 — most of the commands that exist in the interactive UI are
# explicitly disabled in headless ("/foo isn't available in this
# environment"), so this list intentionally excludes /help, /agents,
# /skills, /memory, /mcp, /hooks, /add-dir, /model, /permissions, etc.
#
# Re-probe with ``scripts/probe-claude-commands.sh`` (or rerun the loop
# from the M4.6 commit) when bumping the supported Claude version.
BUILTIN_CC_COMMANDS: list[Suggestion] = [
    Suggestion("/clear", "clear conversation history", "builtin"),
    Suggestion("/compact", "summarise + compress conversation", "builtin"),
    Suggestion("/context", "token usage breakdown", "builtin"),
    Suggestion("/usage", "subscription / billing info", "builtin"),
    Suggestion("/cost", "session cost", "builtin"),
    Suggestion("/review", "review a PR (needs git context)", "builtin"),
    Suggestion("/security-review", "security review of pending changes", "builtin"),
]


# ---------------------------------------------------------------------------
# Filesystem skill discovery
# ---------------------------------------------------------------------------


def _claude_root() -> Path:
    """``$CLAUDE_HOME`` if set, else ``~/.claude``."""
    explicit = os.environ.get("CLAUDE_HOME")
    if explicit:
        return Path(explicit)
    return Path.home() / ".claude"


def list_skills() -> list[Suggestion]:
    """Walk the user's Claude config and return one ``Suggestion`` per skill."""
    out: list[Suggestion] = []
    root = _claude_root()
    if not root.exists():
        return out

    # Direct user skills: ~/.claude/skills/<name>/SKILL.md  or  *.md
    skills_dir = root / "skills"
    out.extend(_scan_skills_dir(skills_dir, source="skill"))

    # Plugin skills: ~/.claude/plugins/<plugin>/skills/<name>/SKILL.md
    plugins_dir = root / "plugins"
    if plugins_dir.exists():
        for plugin in sorted(plugins_dir.iterdir()):
            if not plugin.is_dir():
                continue
            inner = plugin / "skills"
            out.extend(_scan_skills_dir(inner, source=f"plugin:{plugin.name}"))

    return out


def _scan_skills_dir(d: Path, *, source: str) -> list[Suggestion]:
    if not d.exists() or not d.is_dir():
        return []
    out: list[Suggestion] = []
    for child in sorted(d.iterdir()):
        # Two layouts in the wild:
        #  • <skills_dir>/<name>/SKILL.md  (plugin/marketplace shape)
        #  • <skills_dir>/<name>.md        (legacy / inline shape)
        if child.is_dir():
            md = child / "SKILL.md"
            if md.exists():
                out.append(_skill_from_md(md, source=source, fallback_name=child.name))
        elif child.suffix.lower() == ".md":
            out.append(_skill_from_md(child, source=source, fallback_name=child.stem))
    return out


def _skill_from_md(path: Path, *, source: str, fallback_name: str) -> Suggestion:
    name, desc = _parse_frontmatter(path)
    skill_name = name or fallback_name
    return Suggestion(
        label=f"/{skill_name}",
        description=desc[:120],
        source=source,
    )


def _parse_frontmatter(path: Path) -> tuple[str, str]:
    """Light-touch YAML frontmatter parser.

    Doesn't pull in pyyaml — frontmatter for skill files is reliably flat
    ``key: value`` lines. Returns ``(name, description)``; either may be
    empty if the frontmatter doesn't carry it.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ("", "")
    if not text.startswith("---"):
        return ("", "")
    body = text[3:]
    end = body.find("\n---")
    if end == -1:
        return ("", "")
    block = body[:end]
    name = ""
    description = ""
    for raw_line in block.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip().strip('"').strip("'")
        if key == "name":
            name = value
        elif key == "description":
            description = value
    return (name, description)


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------


def slash_suggestions() -> list[Suggestion]:
    """All ``/``-prefixed completions: built-in CC commands + user skills."""
    skills = list_skills()
    # Skills can shadow built-ins (rare, but we trust the user's filesystem).
    seen: dict[str, Suggestion] = {s.label: s for s in BUILTIN_CC_COMMANDS}
    for s in skills:
        seen[s.label] = s
    return sorted(seen.values(), key=lambda s: s.label)


# ---------------------------------------------------------------------------
# Probed `/help` parsing
# ---------------------------------------------------------------------------


# Match bullet rows like:
#   • /name — description
#   • /name - description
#   - /name — short
#   * /name args — desc
_HELP_LINE_RE = re.compile(
    r"^\s*[•*\-]\s*/(?P<name>[A-Za-z][A-Za-z0-9_\-]*)"
    r"(?:\s+\([^)]*\))?"
    r"(?:\s*[—\-:]\s*(?P<desc>.+?))?\s*$"
)


def parse_help_output(text: str) -> list[Suggestion]:
    """Parse Claude Code's ``/help`` reply into a list of available commands.

    The reply is free-form Markdown with bullet rows; we tolerate the
    common variants ``• /foo — bar``, ``* /foo - bar``, and ``- /foo``.
    Lines that don't match are skipped, so non-bullet narration in the
    reply (group headings, blank lines) is harmless.

    Returns an empty list if nothing matched — the caller should treat
    that as "probe failed" and keep the static fallback.
    """
    out: dict[str, Suggestion] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith(("•", "*", "-")):
            continue
        m = _HELP_LINE_RE.match(line)
        if m is None:
            continue
        name = m.group("name")
        desc = (m.group("desc") or "").strip()
        if not name or name in out:
            continue
        out[name] = Suggestion(label=f"/{name}", description=desc[:120], source="probed")
    return sorted(out.values(), key=lambda s: s.label)


def filter_suggestions(items: list[Suggestion], typed: str) -> list[Suggestion]:
    """Substring + prefix-priority filter. ``typed`` includes the prefix."""
    if not typed:
        return items
    needle = typed.lower()
    prefix_hits: list[Suggestion] = []
    substr_hits: list[Suggestion] = []
    for item in items:
        label_lower = item.label.lower()
        if label_lower.startswith(needle):
            prefix_hits.append(item)
        elif needle[1:] and needle[1:] in label_lower:
            substr_hits.append(item)
    return prefix_hits + substr_hits
