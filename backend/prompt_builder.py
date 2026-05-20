"""Prompt builder: assembles the full prompt for each dispatch."""

import re
import json
from pathlib import Path
from .agent_memory import read_agent_memory
from .project import read_project_rules

COUNCIL_ROOT = Path(__file__).resolve().parent.parent
PASTED_IMAGE_MARKDOWN_RE = re.compile(
    r"!\[([^\]\n]*)\]\((?:/api/sessions/[^)\s]+/attachments/[^)\s]+|"
    r"[^)\s]*[\\/]attachments[\\/]paste-[^)\s]+)\)"
)
PASTED_ATTACHMENT_LINE_RE = re.compile(
    r"(?m)^Attachment:\s*`?(?:[^`\n]*[\\/])?attachments[\\/]paste-[^`\n]+`?\s*$"
)


def build_prompt(target_agent: str, project_root: Path, chat_md_path: Path,
                 max_chars: int = 25000, role: str = "",
                 session_dir: Path | None = None,
                 aliases: dict | None = None,
                 compactions_path: Path | None = None,
                 attachment_policy: str | None = None) -> str:
    aliases = aliases or {}
    attachment_policy = _normalize_attachment_policy(target_agent, attachment_policy)
    mention_names = {
        agent: str(aliases.get(agent) or agent).strip().lstrip("@")
        for agent in ("claude", "codex", "deepseek")
    }
    mention_list = " / ".join(f"@{name}" for name in mention_names.values())
    plain_list = ", ".join(mention_names.values())
    clauses: list[str] = []

    clauses.append(
        "=== SAFETY RULES (hard — violations abort) ===\n"
        "- DO NOT delete the repo, folders, or files.\n"
        "- DO NOT touch .git/, .claude/, ProjectSettings/, Library/.\n"
        "- DO NOT run destructive git commands.\n"
        "- You are a local CLI subprocess. Use the filesystem access available "
        "to your CLI naturally, including the target project and Council app "
        "when relevant.\n"
        "- Do not make unrelated edits. If the requested target is unclear, "
        "ask a concise question and stop.\n"
    )

    project_rules = read_project_rules(project_root)
    clauses.append(
        "=== PROJECT RULES (from AGENTS.md or CLAUDE.md) ===\n" + project_rules
    )

    clauses.append(
        "=== HOW THIS CHAT WORKS ===\n"
        "You are participating in a shared council chat for the current "
        "Council session. "
        "The user and other agents speak in turns. "
        f"Council itself is the orchestration app at "
        f"{COUNCIL_ROOT}. The current project root is the "
        "target project being discussed, not necessarily Council's own code. "
        "If the user asks about Council, orchestrator, dispatch, permissions, "
        "or process behavior, discuss that as Council infrastructure. Do not "
        "mislabel it as a game-dev task.\n"
        + _attachment_instruction(attachment_policy) +
        "When you respond, you MUST:\n"
        "1. Write your response as a single turn body.\n"
        "2. If you want another agent to respond next, mention them with "
        f"{mention_list} anywhere in your response. "
        f"3. Treat {mention_list} as activation commands, not "
        f"casual names. If you are only referring to an agent, write {plain_list} "
        "without @.\n"
        "4. If you do not include an activation mention, Council will stop "
        "dispatching after your turn and return control to the user. "
        "Do not announce that the chain is stopping.\n"
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

    turn_clause = (
        f"=== YOUR TURN ===\n"
        f"You are {target_agent}. Respond to the chat above.\n"
    )
    pinned_summary = _read_latest_compaction_summary(
        chat_md_path,
        compactions_path,
        max_chars=max(3000, max_chars // 3),
    )
    if pinned_summary:
        clauses.append(
            "=== SHARED COMPACTED CONTEXT (authoritative) ===\n"
            "This is shared Council context from the latest compaction. Treat "
            "it as higher authority than private memory unless the latest chat "
            "explicitly corrects it.\n\n"
            + _apply_attachment_policy(pinned_summary, attachment_policy)
        )

    agent_memory = ""
    if session_dir is not None:
        agent_memory = read_agent_memory(
            session_dir,
            target_agent,
            max_chars=max(1500, min(5000, max_chars // 5)),
        )

    pre_memory = "\n".join(clauses)
    memory_budget = max(0, min(5000, max_chars - len(pre_memory) - len(turn_clause) - 4500))
    if agent_memory.strip() and memory_budget > 500:
        clauses.append(
            "=== YOUR PRIVATE EFFORT MEMORY ===\n"
            "This memory belongs only to this agent. It may include "
            "partial work, cancelled-run notes, prior command output, "
            "or artifacts from your own earlier runs. Use it for "
            "continuity, but do not treat it as shared consensus unless "
            "the same information appears in the shared compacted context "
            "or latest chat.\n\n"
            + _trim_from_start(agent_memory, memory_budget)
        )

    recent_actions = _read_recent_agent_actions(session_dir, max_chars=1800)
    if recent_actions:
        clauses.append(
            "=== RECENT AGENT TOOL ACTIVITY ===\n"
            "Compact provenance from agent turns since the latest user message. "
            "Use it as pointers to what changed or was inspected; read files "
            "yourself before making edits.\n\n"
            + recent_actions
        )

    preamble = "\n".join(clauses)
    chat_budget = max(1500, max_chars - len(preamble) - len(turn_clause) - 300)
    chat_tail = _apply_attachment_policy(
        _read_chat_tail(chat_md_path, chat_budget),
        attachment_policy,
    )
    clauses.append(
        "=== CHAT TAIL (last portion of conversation) ===\n" + chat_tail
    )

    clauses.append(turn_clause)

    prompt = "\n".join(clauses)
    if len(prompt) > max_chars:
        overflow = len(prompt) - max_chars
        chat_tail = _trim_from_start(chat_tail, max(1000, len(chat_tail) - overflow - 200))
        clauses[-2] = "=== CHAT TAIL (last portion of conversation) ===\n" + chat_tail
        prompt = "\n".join(clauses)
    return prompt


def _read_chat_tail(chat_md_path: Path, max_chars: int = 25000) -> str:
    if not chat_md_path.exists():
        return "(no chat yet)"

    full = chat_md_path.read_text(encoding="utf-8", errors="replace")
    tail_limit = min(20000, max_chars)

    if len(full) <= tail_limit:
        chat_part = full
    else:
        snippet = full[-tail_limit:]
        headers = list(re.finditer(r"(?m)^##\s+\[@\w+\]\s+.+$", snippet))
        if headers:
            snippet = snippet[headers[0].start():]
        chat_part = snippet

    if len(chat_part) < len(full):
        chat_part = ("(earlier conversation omitted)\n\n" + chat_part.strip())

    return chat_part


def _read_recent_agent_actions(session_dir: Path | None, max_chars: int = 1800) -> str:
    if session_dir is None:
        return ""
    events_path = session_dir / "events.jsonl"
    if not events_path.exists():
        return ""

    turns: list[dict] = []
    for line in events_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("kind") in ("user_turn", "agent_turn"):
            turns.append(event)

    latest_user_idx = -1
    for idx, event in enumerate(turns):
        if event.get("kind") == "user_turn":
            latest_user_idx = idx

    lines: list[str] = []
    relevant_turns = turns[latest_user_idx + 1:]
    if not any(event.get("kind") == "agent_turn" for event in relevant_turns):
        relevant_turns = []
        for event in reversed(turns[:latest_user_idx]):
            if event.get("kind") == "user_turn":
                break
            relevant_turns.append(event)
        relevant_turns.reverse()

    for event in relevant_turns:
        if event.get("kind") != "agent_turn":
            continue
        metadata = event.get("metadata") or {}
        tool_calls = metadata.get("tool_calls") or []
        if not isinstance(tool_calls, list) or not tool_calls:
            continue
        author = event.get("author") or "agent"
        display_ts = event.get("display_ts") or ""
        lines.append(f"- {author} ({display_ts}):")
        for item in tool_calls[:8]:
            if not isinstance(item, dict):
                continue
            kind = item.get("kind") or "tool"
            label = item.get("label") or item.get("detail") or "tool activity"
            paths = item.get("paths") or []
            path_text = f" [{', '.join(map(str, paths[:3]))}]" if paths else ""
            lines.append(f"  - {kind}: {label}{path_text}")

    return _trim_from_start("\n".join(lines).strip(), max_chars)


def _sanitize_pasted_attachments(text: str) -> str:
    """Hide pasted image URLs/paths from agent prompts while preserving presence."""
    if not text:
        return text

    def image_replacement(match: re.Match) -> str:
        alt = (match.group(1) or "pasted image").strip() or "pasted image"
        return f"[image attachment present: {alt}; contents not included in agent prompt]"

    text = PASTED_IMAGE_MARKDOWN_RE.sub(image_replacement, text)
    return PASTED_ATTACHMENT_LINE_RE.sub(
        "[image attachment path hidden from agent prompt]",
        text,
    )


def _normalize_attachment_policy(target_agent: str, attachment_policy: str | None) -> str:
    policy = (attachment_policy or "").strip().lower()
    if not policy:
        policy = "path-visible" if target_agent in {"claude", "codex"} else "placeholder"
    if policy not in {"placeholder", "path-visible"}:
        return "placeholder"
    return policy


def _attachment_instruction(attachment_policy: str) -> str:
    if attachment_policy == "path-visible":
        return (
            "Pasted images may appear in chat with an Attachment path. "
            "Only open or inspect an attachment when the user explicitly asks "
            "about the image contents or asks you to inspect a local image "
            "file. Otherwise, treat it as context that an image exists and do "
            "not spend tokens reading it.\n"
        )
    return (
        "Pasted images may appear in chat as attachment placeholders only. "
        "Acknowledge that an image exists when relevant, but do not claim to "
        "see or inspect image contents unless the user explicitly provides a "
        "text description or asks you to inspect a local image file.\n"
    )


def _apply_attachment_policy(text: str, attachment_policy: str) -> str:
    if attachment_policy == "path-visible":
        return text
    return _sanitize_pasted_attachments(text)


def _trim_from_start(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    snippet = text[-max_chars:]
    header = "(earlier content omitted)\n"
    return header + snippet[len(header):]


def _read_latest_compaction_summary(
    chat_md_path: Path,
    compactions_path: Path | None,
    max_chars: int,
) -> str:
    summary = _latest_compaction_record_summary(compactions_path)
    if not summary:
        summary = _compaction_prefix_from_chat(chat_md_path)
    return _trim_from_start(summary.strip(), max_chars).strip()


def _latest_compaction_record_summary(compactions_path: Path | None) -> str:
    if not compactions_path or not compactions_path.exists():
        return ""
    latest: dict | None = None
    for line in compactions_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            latest = record
    if not latest:
        return ""
    archive = latest.get("summary_path") or "chat-archive/unknown.md"
    created_at = latest.get("created_at") or "unknown time"
    body = str(latest.get("summary") or "").strip()
    if not body:
        return ""
    return f"Compacted at {created_at}. Archive: `{archive}`\n\n{body}"


def _compaction_prefix_from_chat(chat_md_path: Path) -> str:
    if not chat_md_path.exists():
        return ""
    text = chat_md_path.read_text(encoding="utf-8", errors="replace")
    header_re = re.compile(r"^##\s+\[@(\w+)\]\s+(.+)$", re.MULTILINE)
    matches = list(header_re.finditer(text))
    if not matches:
        return ""
    first = matches[0]
    if first.group(1) != "system" or not first.group(2).startswith("compacted "):
        return ""
    end = matches[1].start() if len(matches) > 1 else len(text)
    return text[:end].strip()
