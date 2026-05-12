#!/usr/bin/env python3
"""Council App: local browser-based multi-agent chat."""

import os
import sys
import tomllib
import logging
from pathlib import Path

import uvicorn

APP_DIR = Path(__file__).resolve().parent
_user_cwd = Path(os.getcwd()).resolve()
os.chdir(APP_DIR)  # ensure backend imports resolve

from backend.state import SessionRegistry
from backend.app import create_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)


def _default_project_root() -> Path:
    if len(sys.argv) > 1:
        return Path(sys.argv[1]).expanduser().resolve()
    return _user_cwd


def main():
    project_root = _default_project_root()

    config_path = APP_DIR / "config.toml"
    with open(config_path, "rb") as f:
        config = tomllib.load(f)

    registry = SessionRegistry(
        council_root=APP_DIR,
        config=config,
        config_path=config_path,
        default_project_root=project_root,
    )
    registry.load_all()

    port = config["server"]["port"]
    host = config["server"]["host"]

    app = create_app(registry)

    print(f"\nCouncil running at http://{host}:{port}")
    print(f"Sessions: {registry.sessions_dir}")
    print(f"Active session: {registry.active_session_id}")
    print(f"Default project for new registries: {project_root}")
    print("Open the URL in your browser. Press Ctrl+C to stop.\n")

    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
