"""Mention parsing helpers for council turn dispatch."""

import re

AGENTS = ("claude", "codex", "deepseek")
DEFAULT_ALIASES = {agent: agent for agent in AGENTS}
FENCED_CODE_RE = re.compile(r"(```|~~~).*?\1", re.DOTALL)
INLINE_CODE_RE = re.compile(r"`+[^`\n]*`+")
ALIAS_RE = re.compile(r"^[a-zA-Z][\w-]{0,31}$")


def _without_markdown_code(text: str) -> str:
    """Remove Markdown code spans/blocks before scanning for activation mentions."""
    text = FENCED_CODE_RE.sub(" ", text)
    return INLINE_CODE_RE.sub(" ", text)


def normalize_aliases(aliases: dict | None = None) -> dict[str, str]:
    """Return alias -> canonical agent mapping, preserving canonical mentions."""
    normalized = {agent: agent for agent in AGENTS}
    if not isinstance(aliases, dict):
        return normalized
    for agent in AGENTS:
        alias = str(aliases.get(agent) or "").strip().lstrip("@").lower()
        if alias and ALIAS_RE.match(alias):
            normalized[alias] = agent
    return normalized


def _mention_re(aliases: dict | None = None, tail: bool = False) -> re.Pattern:
    alias_map = normalize_aliases(aliases)
    names = sorted(alias_map, key=len, reverse=True)
    pattern = r"@(" + "|".join(re.escape(name) for name in names) + r")(?!\w)"
    if tail:
        pattern += r"\s*$"
    return re.compile(pattern, re.IGNORECASE)


def find_tail_mention(body: str, aliases: dict | None = None) -> str | None:
    """Return the agent mentioned at the tail of text, if any."""
    alias_map = normalize_aliases(aliases)
    match = _mention_re(aliases, tail=True).search(_without_markdown_code(body).strip())
    return alias_map.get(match.group(1).lower()) if match else None


def find_first_mention(body: str, aliases: dict | None = None) -> str | None:
    """Return the first agent mention in a turn, if any."""
    alias_map = normalize_aliases(aliases)
    match = _mention_re(aliases).search(_without_markdown_code(body))
    return alias_map.get(match.group(1).lower()) if match else None


def find_agent_mentions(body: str, aliases: dict | None = None) -> list[str]:
    """Return distinct activation mentions in first-seen order."""
    alias_map = normalize_aliases(aliases)
    mentions: list[str] = []
    for match in _mention_re(aliases).finditer(_without_markdown_code(body)):
        agent = alias_map.get(match.group(1).lower())
        if agent not in mentions:
            mentions.append(agent)
    return mentions


def neutralize_agent_mentions(text: str, aliases: dict | None = None) -> str:
    """Make agent mentions inert before writing system-owned diagnostic turns."""
    return _mention_re(aliases).sub(lambda m: f"{m.group(1)}", text)
