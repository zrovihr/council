"""Per-agent effort memory for cancelled, failed, and completed runs."""

import json
import re
import secrets
from datetime import datetime
from pathlib import Path

from .state import AGENTS, clean_trace_text

MAX_CURRENT_CHARS = 12000
MAX_ARTIFACT_CHARS = 60000
MAX_PRIOR_CURRENT_CHARS = 5000


def _stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d-%H%M%S")


def _tail(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return "(earlier private memory/output omitted due size limit; consult archived artifacts if needed)\n" + text[-max_chars:]


def _drop_section(text: str, heading: str) -> str:
    pattern = re.compile(
        rf"(?ms)^## {re.escape(heading)}\n.*?(?=^## |\Z)"
    )
    return pattern.sub("", text).strip()


def _drop_stream_blocks(text: str) -> str:
    pattern = re.compile(
        r"(?ms)^\[(?:stdout|stderr|opencode stdout|opencode stderr)\]\n.*?(?=^\[|^## |\Z)"
    )
    return pattern.sub("", text).strip()


def _prompt_safe_memory(text: str) -> str:
    """Remove bulky CLI stream echoes before memory is inserted into prompts."""
    if not text:
        return ""
    text = _drop_section(text, "Last Captured Output")
    text = _drop_stream_blocks(text)
    return text.strip()


def _prior_current_excerpt(current_path: Path) -> str:
    if not current_path.exists():
        return ""
    text = _prompt_safe_memory(
        current_path.read_text(encoding="utf-8", errors="replace")
    ).strip()
    if not text:
        return ""
    return _tail(text, MAX_PRIOR_CURRENT_CHARS)


def agent_memory_root(session_dir: Path) -> Path:
    return session_dir / "agent-memory"


def ensure_agent_memory_dirs(session_dir: Path) -> None:
    root = agent_memory_root(session_dir)
    for agent in AGENTS:
        (root / agent / "artifacts").mkdir(parents=True, exist_ok=True)
        runs_path = root / agent / "runs.jsonl"
        runs_path.touch(exist_ok=True)


def _display_path(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def read_agent_memory(session_dir: Path, agent: str, max_chars: int = MAX_CURRENT_CHARS) -> str:
    if agent not in AGENTS:
        return ""
    current_path = agent_memory_root(session_dir) / agent / "current.md"
    if not current_path.exists():
        return ""
    text = _prompt_safe_memory(current_path.read_text(encoding="utf-8", errors="replace"))
    return _tail(text, max_chars)


def write_agent_memory(
    session_dir: Path,
    agent: str,
    status: str,
    final_response: str = "",
    captured_output: str = "",
    error: str = "",
    usage: dict[str, int] | None = None,
    project_root: Path | None = None,
) -> Path | None:
    if agent not in AGENTS:
        return None

    ensure_agent_memory_dirs(session_dir)
    root = agent_memory_root(session_dir) / agent
    current_path = root / "current.md"
    prior_current = _prior_current_excerpt(current_path)
    artifact_id = f"{_stamp()}-{secrets.token_hex(3)}"
    artifact_path = root / "artifacts" / f"{artifact_id}.md"

    final_response = clean_trace_text(final_response).strip()
    captured_output = clean_trace_text(captured_output).strip()
    error = clean_trace_text(error).strip()

    artifact_sections = [
        f"# @{agent} run artifact",
        f"- status: {status}",
        f"- created_at: {datetime.now().isoformat(timespec='seconds')}",
    ]
    if usage:
        artifact_sections.append(f"- usage: `{json.dumps(usage, ensure_ascii=True)}`")
    if error:
        artifact_sections.extend(["", "## Error", "```text", _tail(error, 8000), "```"])
    if final_response:
        artifact_sections.extend(["", "## Final Response", final_response])
    if captured_output:
        artifact_sections.extend([
            "",
            "## Captured Output",
            "```text",
            _tail(captured_output, MAX_ARTIFACT_CHARS),
            "```",
        ])

    artifact_path.write_text("\n".join(artifact_sections).rstrip() + "\n", encoding="utf-8")

    current_sections = [
        f"# @{agent} private effort memory",
        "",
        f"Latest run status: `{status}`",
        f"Latest artifact: `{_display_path(artifact_path, project_root if project_root is not None else session_dir)}`",
        f"Updated: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "This is private continuity for this same agent. Use it to avoid re-reading or re-deriving prior work, but prefer the shared chat for decisions visible to everyone.",
    ]
    if prior_current:
        current_sections.extend([
            "",
            "## Prior Continuity",
            prior_current,
        ])
    current_sections.extend(["", "## Latest Run"])
    if error:
        current_sections.extend(["", "## Last Error", "```text", _tail(error, 3000), "```"])
    if final_response:
        current_sections.extend(["", "## Last Final Response", _tail(final_response, 5000)])
    current_path.write_text(
        _tail("\n".join(current_sections).rstrip() + "\n", MAX_CURRENT_CHARS),
        encoding="utf-8",
    )

    run_record = {
        "id": artifact_id,
        "agent": agent,
        "status": status,
        "artifact": str(artifact_path.relative_to(session_dir)),
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    if usage:
        run_record["usage"] = usage
    with open(root / "runs.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(run_record, ensure_ascii=True) + "\n")

    return artifact_path
