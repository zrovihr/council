"""Summarizer: compact chat.md via Deepseek Flash."""

import asyncio
import logging
import shutil
from datetime import datetime
from pathlib import Path

from .dispatcher import (
    _run_opencode_with_prompt_file,
    _strip_ansi,
    _strip_opencode_header,
)
from .mentions import find_tail_mention

logger = logging.getLogger(__name__)


async def compact_chat(chat_path: Path, archive_dir: Path, config: dict) -> None:
    if not chat_path.exists():
        return

    full_text = chat_path.read_text(encoding="utf-8", errors="replace")

    pending_mention = find_tail_mention(full_text)

    timestamp = datetime.now().strftime("%Y-%m-%d-%H%M")
    archive_name = f"chat-{timestamp}.md"
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
