#!/usr/bin/env python3
"""Council App: Local browser-based multi-agent chat.

Entry point. Run from any project root that has a CLAUDE.md.
"""

import sys
import os
import tomllib
import logging
import shutil
from datetime import datetime
from pathlib import Path

import uvicorn

APP_DIR = Path(__file__).resolve().parent
_user_cwd = Path(os.getcwd()).resolve()
os.chdir(APP_DIR)  # ensure backend imports resolve

from backend.state import AppState, get_archive_dir, get_chat_path
from backend.app import create_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)


def _rotate_previous_chat(chat_md: Path, archive_dir: Path) -> None:
    if not chat_md.exists() or not chat_md.read_text(
        encoding="utf-8", errors="replace"
    ).strip():
        return

    timestamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / f"chat-session-{timestamp}.md"
    counter = 2
    while archive_path.exists():
        archive_path = archive_dir / f"chat-session-{timestamp}-{counter}.md"
        counter += 1
    shutil.move(str(chat_md), str(archive_path))


def main():
    project_root = _user_cwd
    claude_md = project_root / "CLAUDE.md"

    if not claude_md.exists():
        print(
            "Error: no CLAUDE.md found in current directory. "
            "cd into a project root and try again."
        )
        sys.exit(1)

    chat_md = get_chat_path(project_root)
    archive_dir = get_archive_dir(project_root)
    chat_md.parent.mkdir(parents=True, exist_ok=True)

    legacy_chat_md = project_root / "ClaudeDocs" / "_handoff" / "chat.md"
    if not chat_md.exists() and legacy_chat_md.exists():
        shutil.copy2(str(legacy_chat_md), str(chat_md))

    _rotate_previous_chat(chat_md, archive_dir)
    if not chat_md.exists():
        chat_md.write_text("", encoding="utf-8")

    config_path = APP_DIR / "config.toml"
    with open(config_path, "rb") as f:
        config = tomllib.load(f)

    port = config["server"]["port"]
    host = config["server"]["host"]

    state = AppState(
        project_root=project_root,
        project_name=project_root.name,
        chat_path=chat_md,
        archive_dir=archive_dir,
        config=config,
        config_path=config_path,
    )

    app = create_app(state)

    print(f"\nCouncil running at http://{host}:{port}")
    print(f"Project: {project_root.name}")
    print(f"current session chat: {chat_md}")
    print(f"previous sessions: {archive_dir}")
    print("Open the URL in your browser. Press Ctrl+C to stop.\n")

    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
