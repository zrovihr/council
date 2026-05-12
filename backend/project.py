"""Project file reader."""

from pathlib import Path


def read_claude_md(project_root: Path) -> str:
    """Read the full contents of CLAUDE.md in the project root."""
    path = project_root / "CLAUDE.md"
    if not path.exists():
        raise FileNotFoundError(f"{path} not found")
    return path.read_text(encoding="utf-8", errors="replace")
