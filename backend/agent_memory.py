"""Per-agent effort memory for cancelled, failed, and completed runs."""

import json
import secrets
from datetime import datetime
from pathlib import Path

from .state import AGENTS, clean_trace_text

MAX_CURRENT_CHARS = 12000
MAX_ARTIFACT_CHARS = 60000


def _stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d-%H%M%S")


def _tail(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return "(earlier output omitted)\n" + text[-max_chars:]


def agent_memory_root(session_dir: Path) -> Path:
    return session_dir / "agent-memory"


def ensure_agent_memory_dirs(session_dir: Path) -> None:
    root = agent_memory_root(session_dir)
    for agent in AGENTS:
        (root / agent / "artifacts").mkdir(parents=True, exist_ok=True)
        runs_path = root / agent / "runs.jsonl"
        runs_path.touch(exist_ok=True)


def read_agent_memory(session_dir: Path, agent: str, max_chars: int = MAX_CURRENT_CHARS) -> str:
    if agent not in AGENTS:
        return ""
    current_path = agent_memory_root(session_dir) / agent / "current.md"
    if not current_path.exists():
        return ""
    return _tail(current_path.read_text(encoding="utf-8", errors="replace"), max_chars)


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
        f"Latest artifact: `{artifact_path.relative_to(project_root if project_root is not None else session_dir)}`",
        f"Updated: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "This is private continuity for this same agent. Use it to avoid re-reading or re-deriving prior work, but prefer the shared chat for decisions visible to everyone.",
    ]
    if error:
        current_sections.extend(["", "## Last Error", "```text", _tail(error, 3000), "```"])
    if final_response:
        current_sections.extend(["", "## Last Final Response", _tail(final_response, 5000)])
    if captured_output:
        current_sections.extend([
            "",
            "## Last Captured Output",
            "```text",
            _tail(captured_output, 5000),
            "```",
        ])

    (root / "current.md").write_text(
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
