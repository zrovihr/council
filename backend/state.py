"""Session registry and shared per-session state."""

import asyncio
import json
import logging
import secrets
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

AGENTS = ("claude", "codex", "deepseek")
STATE_SECTIONS = ("models", "effort", "roles")


def utc_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _json_default(path: Path, default: dict) -> dict:
    if not path.exists():
        return default.copy()
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("Failed to read JSON file %s", path)
        return default.copy()
    return loaded if isinstance(loaded, dict) else default.copy()


def default_session_state() -> dict:
    return {"models": {}, "effort": {}, "roles": {}}


def normalize_project_root(project_root: str | Path) -> Path:
    return Path(project_root).expanduser().resolve()


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


def overlay_config(config: dict, session_state: dict | None) -> dict:
    """Return a shallow config copy with non-empty per-session overrides."""
    merged = {
        key: value.copy() if isinstance(value, dict) else value
        for key, value in config.items()
    }
    if not isinstance(session_state, dict):
        return merged

    for section in STATE_SECTIONS:
        overrides = session_state.get(section)
        if not isinstance(overrides, dict):
            continue
        target = merged.setdefault(section, {})
        for key, value in overrides.items():
            if value is None or value == "":
                continue
            target_key = "deepseek_pro" if section == "models" and key == "deepseek" else key
            target[target_key] = str(value)
    return merged


def build_agent_info(config: dict, session_state: dict | None = None) -> dict:
    effective = overlay_config(config, session_state)
    models = effective.get("models", {})
    binaries = effective.get("binaries", {})
    efforts = effective.get("effort", {})
    roles = effective.get("roles", {})
    model_options = effective.get("model_options", {})
    effort_options = effective.get("effort_options", {})
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
            "note": "Configured in Council config.toml or this session.",
        },
    }


def render_turn(author: str, text: str, timestamp: str | None = None) -> str:
    stamp = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"\n## [@{author}] {stamp}\n{text}\n\n---\n"


def rebuild_chat_from_events(events_path: Path, chat_path: Path) -> None:
    if not events_path.exists():
        chat_path.write_text("", encoding="utf-8")
        return
    parts: list[str] = []
    for line in events_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("kind") not in ("user_turn", "agent_turn", "system_turn"):
            continue
        author = event.get("author") or "system"
        text = event.get("text") or ""
        parts.append(render_turn(author, text, event.get("display_ts")))
    chat_path.write_text("".join(parts), encoding="utf-8")


@dataclass
class Session:
    id: str
    name: str
    project_root: Path
    session_dir: Path
    config: dict
    meta_path: Path
    state_path: Path
    chat_path: Path
    events_path: Path
    compactions_path: Path
    archive_dir: Path
    state: dict = field(default_factory=default_session_state)
    created_at: str = field(default_factory=utc_now)
    last_used_at: str = field(default_factory=utc_now)
    busy: bool = False
    current_agent: Optional[str] = None
    current_dispatch_task: Optional[asyncio.Task] = None
    trace_events: list[dict] = field(default_factory=list)
    subscribers: list = field(default_factory=list)

    @property
    def project_name(self) -> str:
        return self.project_root.name or str(self.project_root)

    def metadata(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "project_root": str(self.project_root),
            "created_at": self.created_at,
            "last_used_at": self.last_used_at,
        }

    def effective_config(self) -> dict:
        return overlay_config(self.config, self.state)

    def ensure_files(self) -> None:
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        self.events_path.touch(exist_ok=True)
        if not self.state_path.exists():
            self.save_state()
        if not self.meta_path.exists():
            self.save_meta()
        if not self.chat_path.exists():
            rebuild_chat_from_events(self.events_path, self.chat_path)

    def save_meta(self) -> None:
        self.meta_path.write_text(
            json.dumps(self.metadata(), indent=2) + "\n",
            encoding="utf-8",
        )

    def save_state(self) -> None:
        clean = default_session_state()
        for section in STATE_SECTIONS:
            values = self.state.get(section)
            clean[section] = values.copy() if isinstance(values, dict) else {}
        self.state = clean
        self.state_path.write_text(
            json.dumps(self.state, indent=2) + "\n",
            encoding="utf-8",
        )

    def touch(self) -> None:
        self.last_used_at = utc_now()
        self.save_meta()

    def append_event(self, event: dict) -> None:
        event = {"ts": utc_now(), **event}
        with open(self.events_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=True) + "\n")

    def append_turn(
        self,
        author: str,
        text: str,
        kind: str | None = None,
        usage: dict[str, int] | None = None,
    ) -> None:
        display_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        body = text.rstrip()
        event_kind = kind or ("user_turn" if author == "you" else "agent_turn")
        event = {
            "kind": event_kind,
            "author": author,
            "text": body,
            "display_ts": display_ts,
        }
        if usage:
            if "prompt_tokens" in usage:
                event["prompt_tokens_est"] = usage["prompt_tokens"]
            if "completion_tokens" in usage:
                event["response_tokens_est"] = usage["completion_tokens"]
            event["token_usage"] = usage
        self.append_event(event)
        with open(self.chat_path, "a", encoding="utf-8") as f:
            f.write(render_turn(author, body, display_ts))
        self.touch()

    async def broadcast_status(self):
        await self.broadcast({
            "type": "status",
            "busy": self.busy,
            "agent": self.current_agent,
            "current_agent": self.current_agent,
            "project": self.project_name,
            "session_id": self.id,
            "agents": build_agent_info(self.config, self.state),
        })

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
        await self.broadcast({"type": "chat_update", "session_id": self.id})

    async def add_trace(self, agent: str, message: str, detail: str = ""):
        event = {
            "time": datetime.now().strftime("%H:%M:%S"),
            "agent": agent,
            "message": message,
            "detail": detail,
        }
        self.trace_events.append(event)
        self.trace_events = self.trace_events[-100:]
        self.append_event({
            "kind": "trace",
            "agent": agent,
            "message": message,
            "detail": detail,
        })
        await self.broadcast({"type": "trace_update", "session_id": self.id, "event": event})

    async def update_config(self, changes: dict) -> None:
        for section in STATE_SECTIONS:
            section_changes = changes.get(section)
            if not isinstance(section_changes, dict):
                continue
            target = self.state.setdefault(section, {})
            for key, value in section_changes.items():
                if key not in AGENTS:
                    continue
                target[key] = str(value)
        self.save_state()
        await self.add_trace("system", "session config updated", ", ".join(changes.keys()))
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


class SessionRegistry:
    def __init__(
        self,
        council_root: Path,
        config: dict,
        config_path: Optional[Path],
        default_project_root: Path,
    ):
        self.council_root = council_root
        self.sessions_dir = council_root / "sessions"
        self.registry_path = self.sessions_dir / "sessions.json"
        self.config = config
        self.config_path = config_path
        self.default_project_root = default_project_root
        self.sessions: dict[str, Session] = {}
        self.active_session_id: str | None = None

    def load_all(self) -> None:
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        registry = _json_default(
            self.registry_path,
            {"sessions": [], "active_session": None},
        )
        self.sessions.clear()
        for meta in registry.get("sessions", []):
            if not isinstance(meta, dict) or not meta.get("id"):
                continue
            session = self._load_session(meta)
            session.ensure_files()
            self.sessions[session.id] = session

        self.active_session_id = registry.get("active_session")
        if self.active_session_id not in self.sessions:
            self.active_session_id = next(iter(self.sessions), None)

        if not self.sessions:
            session = self.create_session("default", self.default_project_root, activate=True)
            self.active_session_id = session.id
        self.save_registry()

    def _load_session(self, meta: dict) -> Session:
        session_id = str(meta["id"])
        session_dir = self.sessions_dir / session_id
        meta_path = session_dir / "meta.json"
        saved_meta = _json_default(meta_path, meta)
        saved_state = _json_default(session_dir / "state.json", default_session_state())
        return Session(
            id=session_id,
            name=str(saved_meta.get("name") or session_id),
            project_root=normalize_project_root(
                saved_meta.get("project_root") or self.default_project_root
            ),
            session_dir=session_dir,
            config=self.config,
            meta_path=meta_path,
            state_path=session_dir / "state.json",
            chat_path=session_dir / "chat.md",
            events_path=session_dir / "events.jsonl",
            compactions_path=session_dir / "compactions.jsonl",
            archive_dir=session_dir / "chat-archive",
            state=saved_state,
            created_at=str(saved_meta.get("created_at") or utc_now()),
            last_used_at=str(saved_meta.get("last_used_at") or utc_now()),
        )

    def _new_session_id(self) -> str:
        while True:
            session_id = f"s_{secrets.token_hex(4)}"
            if session_id not in self.sessions and not (self.sessions_dir / session_id).exists():
                return session_id

    def create_session(
        self,
        name: str,
        project_root: str | Path,
        activate: bool = True,
    ) -> Session:
        session_id = self._new_session_id()
        session_dir = self.sessions_dir / session_id
        now = utc_now()
        session = Session(
            id=session_id,
            name=name.strip() or "untitled",
            project_root=normalize_project_root(project_root),
            session_dir=session_dir,
            config=self.config,
            meta_path=session_dir / "meta.json",
            state_path=session_dir / "state.json",
            chat_path=session_dir / "chat.md",
            events_path=session_dir / "events.jsonl",
            compactions_path=session_dir / "compactions.jsonl",
            archive_dir=session_dir / "chat-archive",
            created_at=now,
            last_used_at=now,
        )
        session.ensure_files()
        self.sessions[session.id] = session
        if activate:
            self.active_session_id = session.id
        self.save_registry()
        return session

    def delete_session(self, session_id: str) -> None:
        if session_id not in self.sessions:
            raise KeyError(session_id)
        session = self.sessions.pop(session_id)
        shutil.rmtree(session.session_dir, ignore_errors=True)
        if self.active_session_id == session_id:
            self.active_session_id = next(iter(self.sessions), None)
        self.save_registry()

    def get(self, session_id: str) -> Session:
        session = self.sessions.get(session_id)
        if session is None:
            raise KeyError(session_id)
        return session

    def active(self) -> Session:
        if not self.active_session_id:
            raise KeyError("no active session")
        return self.get(self.active_session_id)

    def list(self) -> list[dict]:
        return sorted(
            (session.metadata() for session in self.sessions.values()),
            key=lambda item: item.get("last_used_at", ""),
            reverse=True,
        )

    def set_active(self, session_id: str) -> Session:
        session = self.get(session_id)
        session.touch()
        self.active_session_id = session_id
        self.save_registry()
        return session

    def save_registry(self) -> None:
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "sessions": [self.sessions[sid].metadata() for sid in self.sessions],
            "active_session": self.active_session_id,
        }
        self.registry_path.write_text(
            json.dumps(data, indent=2) + "\n",
            encoding="utf-8",
        )

    async def update_global_config(self, changes: dict) -> None:
        for section in STATE_SECTIONS:
            section_changes = changes.get(section)
            if not isinstance(section_changes, dict):
                continue
            target = self.config.setdefault(section, {})
            for key, value in section_changes.items():
                if key not in AGENTS:
                    continue
                target_key = "deepseek_pro" if section == "models" and key == "deepseek" else key
                target[target_key] = str(value)
        if self.config_path is not None:
            write_config(self.config_path, self.config)
        for session in self.sessions.values():
            await session.add_trace("system", "global config updated", ", ".join(changes.keys()))
            await session.broadcast_status()
