"""Mention parsing helpers for council turn dispatch."""

import re

AGENTS = ("claude", "codex", "deepseek")
TAIL_MENTION_RE = re.compile(r"@(" + "|".join(AGENTS) + r")(?!\w)\s*$")
MENTION_RE = re.compile(r"@(" + "|".join(AGENTS) + r")(?!\w)")


def find_tail_mention(body: str) -> str | None:
    """Return the agent mentioned at the tail of a turn, if any.

    Casual references like "try @codex again" must not dispatch. A turn only
    hands off when the final non-whitespace text is an agent mention.
    """
    match = TAIL_MENTION_RE.search(body.strip())
    return match.group(1) if match else None


def find_first_mention(body: str) -> str | None:
    """Return the first agent mention in a user-authored turn, if any."""
    match = MENTION_RE.search(body)
    return match.group(1) if match else None


def find_agent_mentions(body: str) -> list[str]:
    """Return distinct agent mentions in first-seen order."""
    mentions: list[str] = []
    for match in MENTION_RE.finditer(body):
        agent = match.group(1)
        if agent not in mentions:
            mentions.append(agent)
    return mentions


def neutralize_agent_mentions(text: str) -> str:
    """Make agent mentions inert before writing system-owned diagnostic turns."""
    return MENTION_RE.sub(lambda m: f"{m.group(1)}", text)
