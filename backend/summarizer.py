"""Summarizer: compact a session chat.md via Deepseek Flash."""

import asyncio
import json
import logging
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
from .state import Session

logger = logging.getLogger(__name__)


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

    summary = await _summarize(full_text, config)

    new_content = f"<!-- COMPACTED {timestamp} → see chat-archive/{archive_name} -->\n{summary}\n"

    if pending_mention:
        new_content += (
            f"\n## [@system] compacted {timestamp}\n"
            f"Pending mention: @{pending_mention}\n\n---\n"
        )
    else:
        new_content += f"\n## [@system] compacted {timestamp}\nChat compacted. No pending mentions.\n\n---\n"

    chat_path.write_text(new_content, encoding="utf-8")
    compaction_id = f"c_{secrets.token_hex(4)}"
    record = {
        "id": compaction_id,
        "covered_lines": covered_lines,
        "summary": summary,
        "summary_path": f"chat-archive/{archive_name}",
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    with open(session.compactions_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=True) + "\n")
    session.append_event({
        "kind": "compaction",
        "compaction_id": compaction_id,
        "covered_range": covered_lines,
    })


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
