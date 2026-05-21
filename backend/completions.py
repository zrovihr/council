"""Autocomplete sources for the chat textarea (@agents and @files)."""

import os
import time
from pathlib import Path

AGENTS = ["claude", "codex", "deepseek", "hermes"]

SKIP_DIRS = {
    ".git", "Library", "Temp", "Logs", "obj", "Build", "Builds",
    "node_modules", ".venv", "venv", "__pycache__", ".idea", ".vs",
    "UserSettings", ".plastic", ".cache",
}
SKIP_EXTS = {
    ".meta", ".pyc", ".dll", ".pdb", ".exe", ".so", ".dylib",
    ".asset", ".unity",  # large binary-ish unity files — comment out if you want them
}
MAX_FILES = 5000
CACHE_TTL_SECONDS = 60

_cache: dict[str, tuple[float, list[str]]] = {}


def list_agents(aliases: dict | None = None) -> list[str]:
    result = []
    for agent in AGENTS:
        alias = str((aliases or {}).get(agent) or agent).strip().lstrip("@")
        result.append(alias or agent)
    return result


def list_project_files(project_root: Path) -> list[str]:
    """Walk the project, return forward-slash relative paths. Cached for
    CACHE_TTL_SECONDS to avoid stomping the disk on every keypress."""
    key = str(project_root)
    now = time.time()
    cached = _cache.get(key)
    if cached and (now - cached[0]) < CACHE_TTL_SECONDS:
        return cached[1]

    result: list[str] = []
    root_str = str(project_root)
    for dirpath, dirnames, filenames in os.walk(root_str):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            ext = os.path.splitext(name)[1].lower()
            if ext in SKIP_EXTS:
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root_str).replace("\\", "/")
            result.append(rel)
            if len(result) >= MAX_FILES:
                break
        if len(result) >= MAX_FILES:
            break

    result.sort()
    _cache[key] = (now, result)
    return result
