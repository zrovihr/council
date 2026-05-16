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
from .state import AGENTS, Session

logger = logging.getLogger(__name__)
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
OPENCODE_DONE_RE = re.compile(
    r"^Done\..*Written to.*council-prompt-.*\.md(?:\.compacted)?\.?$"
)
OPENCODE_ECHO_RE = re.compile(
    r"^(?:Read the attached (?:prompt file|transcript)|Read @\w+'s private memory) and.*\.$"
)


async def compact_chat(session: Session, config: dict) -> None:
    chat_path = session.chat_path
    if not chat_path.exists():
        return

    full_text = chat_path.read_text(encoding="utf-8", errors="replace")
    covered_lines = [0, len(full_text.splitlines())]

    pending_mention = find_tail_mention(full_text)

    timestamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    archive_name = f"chat-{timestamp}.md"
    archive_dir = session.archive_dir
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / archive_name

    shutil.move(str(chat_path), str(archive_path))

    summary = _clean_summary(await _summarize(full_text, config))

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

    chat_path.write_text(new_content, encoding="utf-8")
    memory_results = await compact_agent_memories(session, config, timestamp)
    compaction_id = f"c_{secrets.token_hex(4)}"
    record = {
        "id": compaction_id,
        "covered_lines": covered_lines,
        "summary": summary,
        "summary_path": f"chat-archive/{archive_name}",
        "agent_memory": memory_results,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    with open(session.compactions_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=True) + "\n")
    session.append_event({
        "kind": "compaction",
        "compaction_id": compaction_id,
        "covered_range": covered_lines,
        "agent_memory": memory_results,
    })


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

    for agent in AGENTS:
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
                            f"Previous memory archive: `{archive_path.relative_to(session.session_dir)}`\n\n"
                            "This is private continuity for this same agent. Use it to avoid re-reading or re-deriving prior work, but prefer the shared chat for decisions visible to everyone.\n\n"
                            "## Compacted Continuity\n"
                            f"{summary}\n"
                        )
                        current_path.write_text(compacted, encoding="utf-8")
                        result["current"] = {
                            "status": "compacted",
                            "archive": str(archive_path.relative_to(session.session_dir)),
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
        "archive": str(archive_path.relative_to(session.session_dir)),
    }


def _clean_summary(summary: str) -> str:
    cleaned = HTML_COMMENT_RE.sub("", summary or "")
    lines = [
        line for line in cleaned.splitlines()
        if line.strip() != "---"
        and not OPENCODE_DONE_RE.match(line.strip())
        and not OPENCODE_ECHO_RE.match(line.strip())
    ]
    return "\n".join(lines).strip() or "No summary returned."


async def _summarize(full_text: str, config: dict) -> str:
    timestamp = datetime.now().strftime("%Y-%m-%d-%H%M")
    prompt = (
        "Summarize this chat transcript for an LLM that will continue the conversation. "
        "Output format EXACTLY:\n\n"
        f"<!-- COMPACTED {timestamp} → see chat-archive/<old-filename> -->\n"
        "## Summary of prior conversation\n"
        "- Decisions made: <bullets>\n"
        "- Open threads: <bullets, including any pending @mention preserved verbatim>\n"
        "- Files touched recently: <list>\n"
        "- Active task context: <one paragraph>\n"
        "---\n\n"
        "Be terse. Include the LAST @mention exactly as written if one is pending. "
        "Do NOT add any other content.\n\n"
        "Transcript:\n" + full_text
    )

    timeout = config.get("dispatch", {}).get("timeout_seconds", 300)
    flash_model = config.get("models", {}).get(
        "deepseek_flash", "deepseek/deepseek-v4-flash"
    )

    stdout = await _run_opencode_with_prompt_file(
        prompt,
        Path.cwd(),
        timeout,
        flash_model,
        "Read the attached transcript and produce the requested compact summary.",
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

    timeout = config.get("dispatch", {}).get("timeout_seconds", 300)
    flash_model = config.get("models", {}).get(
        "deepseek_flash", "deepseek/deepseek-v4-flash"
    )

    stdout = await _run_opencode_with_prompt_file(
        prompt,
        Path.cwd(),
        timeout,
        flash_model,
        f"Read @{agent}'s private memory and produce the requested compact summary.",
    )
    cleaned = _strip_ansi(stdout)
    cleaned = _strip_opencode_header(cleaned)
    return cleaned.strip()
