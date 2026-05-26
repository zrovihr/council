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
PRIORITY_DIRS = {
    "docs": 0,
    "claudedocs": 0,
    "documentation": 0,
    "src": 1,
    "source": 1,
    "backend": 1,
    "frontend": 1,
    "scripts": 1,
    "tools": 1,
}
DEFERRED_DIRS = {
    "assets": 10,
    "packages": 11,
}

_cache: dict[str, tuple[float, list[str]]] = {}


def list_agents(aliases: dict | None = None, agents: list[str] | None = None) -> list[str]:
    agent_list = agents or list(AGENTS)
    result = []
    for agent in agent_list:
        alias = str((aliases or {}).get(agent) or agent).strip().lstrip("@")
        result.append(alias or agent)
    return result


def _dir_priority(name: str) -> tuple[int, str]:
    low = name.lower()
    if low in PRIORITY_DIRS:
        return (PRIORITY_DIRS[low], low)
    if low in DEFERRED_DIRS:
        return (DEFERRED_DIRS[low], low)
    return (5, low)


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
        dirnames.sort(key=_dir_priority)
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
