"""Mention parsing helpers for council turn dispatch."""

import re

AGENTS = ("claude", "codex", "deepseek")
TAIL_MENTION_RE = re.compile(r"@(" + "|".join(AGENTS) + r")(?!\w)\s*$")
MENTION_RE = re.compile(r"@(" + "|".join(AGENTS) + r")(?!\w)")
FENCED_CODE_RE = re.compile(r"(```|~~~).*?\1", re.DOTALL)
INLINE_CODE_RE = re.compile(r"`+[^`\n]*`+")


def _without_markdown_code(text: str) -> str:
    """Remove Markdown code spans/blocks before scanning for activation mentions."""
    text = FENCED_CODE_RE.sub(" ", text)
    return INLINE_CODE_RE.sub(" ", text)


def find_tail_mention(body: str) -> str | None:
    """Return the agent mentioned at the tail of text, if any."""
    match = TAIL_MENTION_RE.search(_without_markdown_code(body).strip())
    return match.group(1) if match else None


def find_first_mention(body: str) -> str | None:
    """Return the first agent mention in a turn, if any."""
    match = MENTION_RE.search(_without_markdown_code(body))
    return match.group(1) if match else None


def find_agent_mentions(body: str) -> list[str]:
    """Return distinct activation mentions in first-seen order."""
    mentions: list[str] = []
    for match in MENTION_RE.finditer(_without_markdown_code(body)):
        agent = match.group(1)
        if agent not in mentions:
            mentions.append(agent)
    return mentions


def neutralize_agent_mentions(text: str) -> str:
    """Make agent mentions inert before writing system-owned diagnostic turns."""
    return MENTION_RE.sub(lambda m: f"{m.group(1)}", text)
