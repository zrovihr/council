"""Summarizer: compact session chat and per-agent memory via Deepseek Flash."""

import asyncio
import json
import logging
import re
import secrets
import shutil
from datetime import datetime
from pathlib import Path

from .dispatcher import (
    _run_opencode_with_prompt_file,
    _strip_ansi,
    _strip_opencode_header,
)
from .mentions import find_tail_mention
from .state import AGENTS, Session, render_turn
from .chat_markdown import TURN_HEADER_LINE_RE, iter_turn_header_matches

logger = logging.getLogger(__name__)
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
OPENCODE_DONE_RE = re.compile(
    r"^Done\..*Written to.*council-prompt-.*\.md(?:\.compacted)?\.?$"
)
OPENCODE_ECHO_RE = re.compile(
    r"^(?:Read the attached (?:prompt file|transcript)|Read @\w+'s private memory) and.*\.$"
)
DEFAULT_VERBATIM_TAIL_TURNS = 6
DEFAULT_VERBATIM_TAIL_CHARS = 12000


def _display_path(path: Path, root: Path | None) -> str:
    if root is None:
        return str(path)
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


async def compact_chat(session: Session, config: dict) -> None:
    chat_path = session.chat_path
    if not chat_path.exists():
        return

    full_text = chat_path.read_text(encoding="utf-8", errors="replace")
    event_lines_at_start = (
        session.events_path.read_text(encoding="utf-8", errors="replace").splitlines()
        if session.events_path.exists()
        else []
    )
    event_insert_idx = len(event_lines_at_start)
    covered_lines = [0, len(full_text.splitlines())]

    pending_mention = find_tail_mention(full_text, session.effective_config().get("aliases", {}))

    timestamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    archive_name = f"chat-{timestamp}.md"
    archive_dir = session.archive_dir
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / archive_name
    summary = _clean_summary(await _summarize(full_text, config, agents=session._session_agents()))
    summary = _append_recent_verbatim_turns(summary, full_text, config)
    post_compaction_turns = ""
    if chat_path.exists():
        current_text = chat_path.read_text(encoding="utf-8", errors="replace")
        if current_text.startswith(full_text):
            post_compaction_turns = current_text[len(full_text):].strip()
        elif current_text != full_text:
            current_event_lines = (
                session.events_path.read_text(encoding="utf-8", errors="replace").splitlines()
                if session.events_path.exists()
                else []
            )
            post_compaction_turns = _render_turn_events(current_event_lines[event_insert_idx:]).strip()

    archive_note = f"Compacted previous chat. Archive: `chat-archive/{archive_name}`"
    new_content = (
        f"## [@system] compacted {timestamp}\n"
        f"{archive_note}\n\n"
        f"{summary}\n\n"
    )

    if pending_mention:
        new_content += f"Pending mention: @{pending_mention}\n\n"
    else:
        new_content += "No pending mentions.\n\n"

    if post_compaction_turns:
        new_content += post_compaction_turns.rstrip() + "\n\n"

    archive_path.write_text(full_text, encoding="utf-8")
    chat_path.write_text(new_content, encoding="utf-8")
    memory_results = await compact_agent_memories(session, config, timestamp)
    compaction_id = f"c_{secrets.token_hex(4)}"
    created_at = datetime.now().isoformat(timespec="seconds")
    record = {
        "id": compaction_id,
        "covered_lines": covered_lines,
        "summary": summary,
        "summary_path": f"chat-archive/{archive_name}",
        "agent_memory": memory_results,
        "created_at": created_at,
    }
    with open(session.compactions_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=True) + "\n")
    compaction_event = {
        "ts": created_at,
        "kind": "compaction",
        "compaction_id": compaction_id,
        "covered_range": covered_lines,
        "agent_memory": memory_results,
    }
    current_event_lines = (
        session.events_path.read_text(encoding="utf-8", errors="replace").splitlines()
        if session.events_path.exists()
        else []
    )
    compaction_line = json.dumps(compaction_event, ensure_ascii=True)
    if not any(f'"compaction_id": "{compaction_id}"' in line for line in current_event_lines):
        insert_idx = min(event_insert_idx, len(current_event_lines))
        current_event_lines.insert(insert_idx, compaction_line)
        session.events_path.write_text(
            "\n".join(current_event_lines).rstrip() + "\n",
            encoding="utf-8",
        )


async def compact_agent_memories(
    session: Session,
    config: dict,
    timestamp: str | None = None,
) -> list[dict]:
    """Compact each agent's current.md and rotate append-only run logs."""
    timestamp = timestamp or datetime.now().strftime("%Y-%m-%d-%H%M%S")
    results = []
    compact_config = config.get("compact", {})
    max_run_entries = int(compact_config.get("agent_runs_keep_entries", 50))
    max_run_bytes = int(compact_config.get("agent_runs_max_bytes", 100 * 1024))
    min_current_bytes = int(compact_config.get("agent_memory_min_bytes", 4 * 1024))

    for agent in session._session_agents():
        agent_root = session.session_dir / "agent-memory" / agent
        agent_root.mkdir(parents=True, exist_ok=True)
        result = {"agent": agent}

        current_path = agent_root / "current.md"
        if current_path.exists():
            current_text = current_path.read_text(encoding="utf-8", errors="replace")
            if current_text.strip():
                current_bytes = current_path.stat().st_size
                if current_bytes < min_current_bytes:
                    result["current"] = {
                        "status": "skipped_small",
                        "bytes": current_bytes,
                        "min_bytes": min_current_bytes,
                    }
                else:
                    try:
                        memory_archive_dir = agent_root / "memory-archive"
                        memory_archive_dir.mkdir(parents=True, exist_ok=True)
                        archive_path = memory_archive_dir / f"current-{timestamp}.md"
                        shutil.copy2(current_path, archive_path)

                        summary = _clean_summary(await _summarize_agent_memory(agent, current_text, config))
                        compacted = (
                            f"# @{agent} private effort memory\n\n"
                            f"Compacted: {datetime.now().isoformat(timespec='seconds')}\n"
                            f"Previous memory archive: `{_display_path(archive_path, session.project_root)}`\n\n"
                            "This is private continuity for this same agent. Use it to avoid re-reading or re-deriving prior work, but prefer the shared chat for decisions visible to everyone.\n\n"
                            "## Compacted Continuity\n"
                            f"{summary}\n"
                        )
                        current_path.write_text(compacted, encoding="utf-8")
                        result["current"] = {
                            "status": "compacted",
                            "archive": _display_path(archive_path, session.project_root),
                            "bytes": current_bytes,
                        }
                    except Exception as exc:
                        logger.exception("failed to compact @%s memory", agent)
                        result["current"] = {"status": "error", "error": str(exc)}
            else:
                result["current"] = {"status": "empty"}
        else:
            result["current"] = {"status": "missing"}

        run_result = _rotate_agent_runs(
            session,
            agent_root,
            timestamp,
            max_run_entries,
            max_run_bytes,
        )
        result["runs"] = run_result
        results.append(result)

    return results


def _rotate_agent_runs(
    session: Session,
    agent_root: Path,
    timestamp: str,
    max_entries: int,
    max_bytes: int,
) -> dict:
    runs_path = agent_root / "runs.jsonl"
    if not runs_path.exists():
        runs_path.touch(exist_ok=True)
        return {"status": "missing_created"}

    raw = runs_path.read_text(encoding="utf-8", errors="replace")
    lines = [line for line in raw.splitlines() if line.strip()]
    size = runs_path.stat().st_size
    if len(lines) <= max_entries and size <= max_bytes:
        return {"status": "kept", "entries": len(lines), "bytes": size}

    keep_count = max(1, max_entries)
    archived_lines = lines[:-keep_count]
    kept_lines = lines[-keep_count:]
    runs_archive_dir = agent_root / "runs-archive"
    runs_archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = runs_archive_dir / f"runs-{timestamp}.jsonl"
    archive_path.write_text("\n".join(archived_lines).rstrip() + "\n", encoding="utf-8")
    runs_path.write_text("\n".join(kept_lines).rstrip() + "\n", encoding="utf-8")

    return {
        "status": "rotated",
        "archived_entries": len(archived_lines),
        "kept_entries": len(kept_lines),
        "archive": _display_path(archive_path, session.project_root),
    }


def _clean_summary(summary: str) -> str:
    cleaned = HTML_COMMENT_RE.sub("", summary or "")
    lines = [
        line for line in cleaned.splitlines()
        if line.strip() != "---"
        and not OPENCODE_DONE_RE.match(line.strip())
        and not OPENCODE_ECHO_RE.match(line.strip())
    ]
    return _escape_turn_headers("\n".join(lines).strip()) or "No summary returned."


def _escape_turn_headers(text: str) -> str:
    return "\n".join(
        f"\\{line}" if TURN_HEADER_LINE_RE.match(line) else line
        for line in text.splitlines()
    )


def _render_turn_events(event_lines: list[str]) -> str:
    parts: list[str] = []
    for line in event_lines:
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("kind") not in ("user_turn", "agent_turn", "system_turn"):
            continue
        parts.append(render_turn(
            str(event.get("author") or "system"),
            str(event.get("text") or ""),
            str(event.get("display_ts") or "") or None,
        ))
    return "".join(parts)


def _append_recent_verbatim_turns(summary: str, full_text: str, config: dict) -> str:
    compact_config = config.get("compact", {})
    max_turns = int(
        compact_config.get("verbatim_tail_turns", DEFAULT_VERBATIM_TAIL_TURNS)
    )
    max_chars = int(
        compact_config.get("verbatim_tail_chars", DEFAULT_VERBATIM_TAIL_CHARS)
    )
    recent = _recent_verbatim_turns(full_text, max_turns=max_turns, max_chars=max_chars)
    if not recent:
        return summary

    cleaned_summary = summary.rstrip()
    cleaned_summary = re.sub(
        r"\n+## Recent verbatim turns\n(?:.*?\n)*?```text\n.*?\n```\s*$",
        "",
        cleaned_summary,
        flags=re.DOTALL,
    )
    fence = _verbatim_fence(recent)
    return (
        f"{cleaned_summary}\n\n"
        "## Recent verbatim turns\n"
        "These are the latest non-system turns preserved exactly so the next agent "
        "can continue without losing wording or context.\n\n"
        f"{fence}text\n"
        f"{recent}\n"
        f"{fence}"
    )


def _verbatim_fence(text: str) -> str:
    """Return a backtick fence longer than any backtick fence inside text."""
    longest = 2
    for match in re.finditer(r"(?m)^\s*(`{3,})", text or ""):
        longest = max(longest, len(match.group(1)))
    return "`" * (longest + 1)


def _recent_verbatim_turns(full_text: str, max_turns: int, max_chars: int) -> str:
    if max_turns <= 0 or max_chars <= 0:
        return ""

    matches = list(iter_turn_header_matches(full_text))
    turns = []
    for idx, (start, match) in enumerate(matches):
        speaker = match.group(1).strip().lower()
        if speaker == "system":
            continue
        end = matches[idx + 1][0] if idx + 1 < len(matches) else len(full_text)
        turn = full_text[start:end].rstrip()
        if turn:
            turns.append(turn)

    selected = turns[-max_turns:]
    while selected and len("\n\n".join(selected)) > max_chars:
        selected.pop(0)

    return "\n\n".join(selected).strip()


def _deepseek_env(config: dict) -> dict[str, str]:
    api_keys = config.get("api_keys", {}) or {}
    env: dict[str, str] = {}
    if api_keys.get("deepseek"):
        env["DEEPSEEK_API_KEY"] = str(api_keys["deepseek"])
    if api_keys.get("deepseek_flash"):
        env["DEEPSEEK_FLASH_API_KEY"] = str(api_keys["deepseek_flash"])
        env["DEEPSEEK_API_KEY"] = str(api_keys["deepseek_flash"])
    if api_keys.get("openrouter"):
        env["OPENROUTER_API_KEY"] = str(api_keys["openrouter"])
    return env


async def _summarize(full_text: str, config: dict, agents: list[str] | None = None) -> str:
    timestamp = datetime.now().strftime("%Y-%m-%d-%H%M")
    agent_list = agents or list(AGENTS)
    agent_lines = "\n".join(
        f"  - {a.title()}: <one sentence summarizing what {a.title()} did, or 'Did not participate'>"
        for a in agent_list
    )
    prompt = (
        "Summarize this chat transcript for an LLM that will continue the conversation. "
        "Output format EXACTLY:\n\n"
        f"<!-- COMPACTED {timestamp} → see chat-archive/<old-filename> -->\n"
        "## Summary of prior conversation\n"
        "- Decisions made: <bullets>\n"
        "- Unanswered questions: <bullets, include every question from the user that was not answered, quoted or paraphrased fully>\n"
        "- Open threads: <bullets, including any pending @mention preserved verbatim>\n"
        "- Files touched recently: <list>\n"
        "- Active task context: <one paragraph>\n"
        "- What to do next: <bullets, 2-4 actionable items sorted by priority>\n"
        "- What each participant did:\n"
        f"  - @you: <one sentence summarizing what the user did, e.g. requested changes, gave feedback, asked questions>\n"
        f"{agent_lines}\n"
        "---\n\n"
        "Be terse. Include the LAST @mention exactly as written if one is pending. "
        "Do not quote recent turns yourself; Council will append a verbatim recent-turn block after this summary. "
        "Do NOT add any other content.\n\n"
        "Transcript:\n" + full_text
    )

    timeout = int(config.get("dispatch", {}).get("timeout_seconds", 300))
    flash_model = config.get("models", {}).get(
        "deepseek_flash", "deepseek/deepseek-v4-flash"
    )

    stdout = await _run_opencode_with_prompt_file(
        prompt,
        Path.cwd(),
        timeout,
        flash_model,
        "Read the attached transcript and produce the requested compact summary.",
        env=_deepseek_env(config),
    )
    cleaned = _strip_ansi(stdout)
    cleaned = _strip_opencode_header(cleaned)
    return cleaned.strip()


async def _summarize_agent_memory(agent: str, memory_text: str, config: dict) -> str:
    timestamp = datetime.now().strftime("%Y-%m-%d-%H%M")
    prompt = (
        f"Summarize @{agent}'s private effort memory for the same agent to continue later. "
        "Preserve only useful continuity. Do not invent shared decisions unless they appear in the text. "
        "This memory can mix target project work with Council infrastructure/tooling work; keep those categories explicit and separate so future agents do not confuse app/tool implementation notes with game/project decisions. "
        "Output format EXACTLY:\n\n"
        f"<!-- AGENT MEMORY COMPACTED {timestamp} -->\n"
        "- Latest useful status: <one bullet>\n"
        "- Target project continuity: <bullets, or 'none'>\n"
        "- Council/tooling continuity: <bullets, or 'none'>\n"
        "- Files or artifacts to remember: <bullets>\n"
        "- Stale/noisy details dropped: <one terse bullet>\n\n"
        "Be terse. Do NOT include captured terminal spam unless it is essential.\n\n"
        "Private memory:\n" + memory_text
    )

    timeout = int(config.get("dispatch", {}).get("timeout_seconds", 300))
    flash_model = config.get("models", {}).get(
        "deepseek_flash", "deepseek/deepseek-v4-flash"
    )

    stdout = await _run_opencode_with_prompt_file(
        prompt,
        Path.cwd(),
        timeout,
        flash_model,
        f"Read @{agent}'s private memory and produce the requested compact summary.",
        env=_deepseek_env(config),
    )
    cleaned = _strip_ansi(stdout)
    cleaned = _strip_opencode_header(cleaned)
    return cleaned.strip()
