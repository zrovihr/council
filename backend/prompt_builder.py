"""Prompt builder: assembles the full prompt for each dispatch."""

from pathlib import Path
from .project import read_claude_md


def build_prompt(target_agent: str, project_root: Path, chat_md_path: Path,
                 max_chars: int = 25000, role: str = "") -> str:
    clauses: list[str] = []

    clauses.append(
        "=== SAFETY RULES (hard  Eviolations abort) ===\n"
        "- DO NOT delete the repo, folders, or files.\n"
        "- DO NOT touch .git/, .claude/, ProjectSettings/, Library/.\n"
        "- DO NOT run destructive git commands.\n"
        "- You are a local CLI subprocess. Use the filesystem access available "
        "to your CLI naturally, including the target project and Council app "
        "when relevant.\n"
        "- Do not make unrelated edits. If the requested target is unclear, "
        "ask a concise question and stop.\n"
    )

    claude_md = read_claude_md(project_root)
    clauses.append(
        "=== PROJECT RULES (from CLAUDE.md) ===\n" + claude_md
    )

    clauses.append(
        "=== HOW THIS CHAT WORKS ===\n"
        "You are participating in a shared council chat for the current "
        "Council session. "
        "The user and other agents (claude/codex/deepseek) speak in turns. "
        "Council itself is the orchestration app at "
        "<user-home>\\Tools\\council. The current project root is the "
        "target project being discussed, not necessarily Council's own code. "
        "If the user asks about Council, orchestrator, dispatch, permissions, "
        "or process behavior, discuss that as Council infrastructure. Do not "
        "mislabel it as a game-dev task.\n"
        "When you respond, you MUST:\n"
        "1. Write your response as a single turn body.\n"
        "2. If you want another agent to respond next, mention them with "
        "@claude / @codex / @deepseek as the final non-whitespace text in "
        "your response. "
        "3. Treat @claude / @codex / @deepseek as activation commands, not "
        "casual names. If you are only referring to an agent, write claude, "
        "codex, or deepseek without @.\n"
        "4. If no activation mention, the chain stops and the user takes over.\n"
        "5. Do NOT pretend to be a different agent. "
        "Do NOT write a turn for someone else.\n"
        "6. Do NOT include the turn header (`## [@you] ...`) in your output. "
        "The orchestrator adds that.\n"
        "7. Keep responses focused. The chat is the medium; "
        "long monologues bloat the log.\n"
    )

    if role.strip():
        clauses.append(
            "=== ROLE FOR THIS AGENT ===\n"
            f"{role.strip()}\n"
        )

    chat_tail = _read_chat_tail(chat_md_path, max_chars)
    clauses.append(
        "=== CHAT TAIL (last portion of conversation) ===\n" + chat_tail
    )

    clauses.append(
        f"=== YOUR TURN ===\n"
        f"You are {target_agent}. Respond to the chat above.\n"
    )

    prompt = "\n".join(clauses)
    if len(prompt) > max_chars:
        prompt = prompt[-max_chars:]
    return prompt


def _read_chat_tail(chat_md_path: Path, max_chars: int = 25000) -> str:
    if not chat_md_path.exists():
        return "(no chat yet)"

    full = chat_md_path.read_text(encoding="utf-8", errors="replace")
    tail_limit = 20000

    if len(full) <= tail_limit:
        chat_part = full
    else:
        snippet = full[-tail_limit:]
        marker = snippet.find("\n---\n")
        if marker != -1:
            snippet = snippet[marker + 1:]
        chat_part = snippet

    if len(chat_part) < len(full):
        chat_part = ("(earlier conversation omitted)\n\n" + chat_part.strip())

    return chat_part
