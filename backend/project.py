"""Project instruction file reader."""

from pathlib import Path


def read_project_rules(project_root: Path) -> str:
    """Read AGENTS.md or CLAUDE.md from the project root."""
    for name in ("AGENTS.md", "CLAUDE.md"):
        path = project_root / name
        if path.exists():
            return path.read_text(encoding="utf-8", errors="replace")
    raise FileNotFoundError(f"No AGENTS.md or CLAUDE.md found in {project_root}")


def read_claude_md(project_root: Path) -> str:
    """Compatibility name for callers that need project rules."""
    return read_project_rules(project_root)
