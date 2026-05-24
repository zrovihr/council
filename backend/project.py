"""Project instruction file reader."""

from pathlib import Path


def read_project_rules(project_root: Path | None) -> str:
    """Read AGENTS.md or CLAUDE.md from the project root."""
    if project_root is None:
        return (
            "This session has no external project root. Treat the Council "
            "session folder as the working directory for scratch files and "
            "ask a concise question if the user later needs a specific project."
        )
    for name in ("AGENTS.md", "CLAUDE.md"):
        path = project_root / name
        if path.exists():
            return path.read_text(encoding="utf-8", errors="replace")
    return (
        "No AGENTS.md or CLAUDE.md was found in this project. "
        "Use the user's latest request, inspect local files as needed, "
        "and ask a concise question if the task is ambiguous."
    )


def read_claude_md(project_root: Path | None) -> str:
    """Compatibility name for callers that need project rules."""
    return read_project_rules(project_root)
