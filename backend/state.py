"""Shared application state."""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def get_council_dir(project_root: Path) -> Path:
    return project_root / ".council"


def get_chat_path(project_root: Path) -> Path:
    return get_council_dir(project_root) / "chat.md"


def get_archive_dir(project_root: Path) -> Path:
    return get_council_dir(project_root) / "chat-archive"


def build_agent_info(config: dict) -> dict:
    models = config.get("models", {})
    binaries = config.get("binaries", {})
    efforts = config.get("effort", {})
    roles = config.get("roles", {})
    model_options = config.get("model_options", {})
    effort_options = config.get("effort_options", {})
    return {
        "claude": {
            "label": "Claude",
            "runtime": "claude CLI",
            "binary": binaries.get("claude") or models.get("claude") or "claude",
            "model": models.get("claude", ""),
            "effort": efforts.get("claude", ""),
            "role": roles.get("claude", ""),
            "model_options": model_options.get("claude", []),
            "effort_options": effort_options.get("claude", []),
            "note": "Uses the Claude CLI default model unless configured there.",
        },
        "codex": {
            "label": "Codex",
            "runtime": "codex exec",
            "binary": binaries.get("codex") or models.get("codex") or "codex",
            "model": models.get("codex", ""),
            "effort": efforts.get("codex", ""),
            "role": roles.get("codex", ""),
            "model_options": model_options.get("codex", []),
            "effort_options": effort_options.get("codex", []),
            "note": "Uses the Codex CLI default model unless configured there.",
        },
        "deepseek": {
            "label": "Deepseek",
            "runtime": "opencode run",
            "binary": binaries.get("opencode") or models.get("opencode") or "opencode",
            "model": models.get("deepseek_pro", "deepseek/deepseek-v4-pro"),
            "effort": efforts.get("deepseek", ""),
            "role": roles.get("deepseek", ""),
            "model_options": model_options.get("deepseek", []),
            "effort_options": effort_options.get("deepseek", []),
            "note": "Configured in Council config.toml.",
        },
    }


def _toml_quote(value: str) -> str:
    return json.dumps(value)


def write_config(path: Path, config: dict) -> None:
    sections = [
        "server",
        "binaries",
        "models",
        "effort",
        "roles",
        "model_options",
        "effort_options",
        "dispatch",
        "compact",
    ]
    lines: list[str] = []
    for section in sections:
        values = config.get(section)
        if not isinstance(values, dict):
            continue
        if lines:
            lines.append("")
        lines.append(f"[{section}]")
        for key, value in values.items():
            if isinstance(value, str):
                rendered = _toml_quote(value)
            elif isinstance(value, bool):
                rendered = "true" if value else "false"
            elif isinstance(value, list):
                items = ", ".join(_toml_quote(str(v)) for v in value)
                rendered = f"[{items}]"
            else:
                rendered = str(value)
            lines.append(f"{key} = {rendered}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@dataclass
class AppState:
    project_root: Path
    project_name: str
    chat_path: Path
    archive_dir: Path
    config: dict
    config_path: Optional[Path] = None
    busy: bool = False
    current_agent: Optional[str] = None
    current_dispatch_task: Optional[asyncio.Task] = None
    trace_events: list[dict] = field(default_factory=list)
    subscribers: list = field(default_factory=list)

    async def broadcast_status(self):
        event = {
            "type": "status",
            "busy": self.busy,
            "agent": self.current_agent,
            "agents": build_agent_info(self.config),
        }
        dead = []
        for queue in self.subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass
            except Exception:
                dead.append(queue)
        for q in dead:
            if q in self.subscribers:
                self.subscribers.remove(q)

    async def set_busy(self, agent: str):
        self.busy = True
        self.current_agent = agent
        await self.broadcast_status()

    async def set_idle(self):
        self.busy = False
        self.current_agent = None
        await self.broadcast_status()

    async def set_dispatch_task(self, task: asyncio.Task | None):
        self.current_dispatch_task = task

    async def cancel_current_dispatch(self) -> bool:
        task = self.current_dispatch_task
        if task is None or task.done():
            return False
        agent = self.current_agent or "system"
        await self.add_trace(agent, "cancel requested", "Stopping active agent subprocess.")
        task.cancel()
        return True

    async def notify_chat_update(self):
        event = {"type": "chat_update"}
        await self.broadcast(event)

    async def add_trace(self, agent: str, message: str, detail: str = ""):
        event = {
            "time": datetime.now().strftime("%H:%M:%S"),
            "agent": agent,
            "message": message,
            "detail": detail,
        }
        self.trace_events.append(event)
        self.trace_events = self.trace_events[-100:]
        await self.broadcast({"type": "trace_update", "event": event})

    async def update_config(self, changes: dict) -> None:
        for section in ("models", "effort", "roles"):
            section_changes = changes.get(section)
            if not isinstance(section_changes, dict):
                continue
            target = self.config.setdefault(section, {})
            for key, value in section_changes.items():
                if key not in ("claude", "codex", "deepseek", "deepseek_pro"):
                    continue
                target_key = "deepseek_pro" if section == "models" and key == "deepseek" else key
                target[target_key] = str(value)
        if self.config_path is not None:
            write_config(self.config_path, self.config)
        await self.add_trace("system", "config updated", ", ".join(changes.keys()))
        await self.broadcast_status()

    async def broadcast(self, event: dict):
        dead = []
        for queue in self.subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass
            except Exception:
                dead.append(queue)
        for q in dead:
            if q in self.subscribers:
                self.subscribers.remove(q)

    def subscribe(self):
        queue: asyncio.Queue = asyncio.Queue(maxsize=64)
        self.subscribers.append(queue)
        return queue

    def unsubscribe(self, queue):
        if queue in self.subscribers:
            self.subscribers.remove(queue)
