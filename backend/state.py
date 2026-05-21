"""Session registry and shared per-session state."""

import asyncio
import copy
import json
import logging
import re
import secrets
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

AGENTS = ("claude", "codex", "deepseek", "hermes")
STATE_SECTIONS = ("models", "effort", "roles", "dispatch", "providers", "api_keys", "aliases", "ui")
SENSITIVE_SECTIONS = {"api_keys"}
SECRET_KEYS = ("claude", "codex", "deepseek", "hermes", "openrouter", "deepseek_flash")
ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


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
    return {
        "models": {},
        "effort": {},
        "roles": {},
        "dispatch": {},
        "providers": {},
        "api_keys": {},
        "aliases": {},
        "ui": {},
    }


def normalize_project_root(project_root: str | Path) -> Path:
    return Path(project_root).expanduser().resolve()


def _toml_quote(value: str) -> str:
    return json.dumps(value)


def _render_toml_value(value) -> str:
    if isinstance(value, str):
        return _toml_quote(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        items = ", ".join(_toml_quote(str(v)) for v in value)
        return f"[{items}]"
    return str(value)


def write_config(path: Path, config: dict) -> None:
    sections = [
        "server",
        "binaries",
        "models",
        "effort",
        "roles",
        "providers",
        "aliases",
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
        nested_sections: list[tuple[str, dict]] = []
        for key, value in values.items():
            if isinstance(value, dict):
                nested_sections.append((key, value))
                continue
            lines.append(f"{key} = {_render_toml_value(value)}")
        for nested_key, nested_values in nested_sections:
            lines.append("")
            lines.append(f"[{section}.{nested_key}]")
            for key, value in nested_values.items():
                if isinstance(value, dict):
                    continue
                lines.append(f"{key} = {_render_toml_value(value)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def overlay_config(config: dict, session_state: dict | None) -> dict:
    """Return a shallow config copy with non-empty per-session overrides."""
    merged = {
        key: copy.deepcopy(value) if isinstance(value, dict) else value
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
            if section == "dispatch" and isinstance(value, dict):
                nested_target = target.setdefault(key, {})
                if not isinstance(nested_target, dict):
                    nested_target = {}
                    target[key] = nested_target
                for nested_key, nested_value in value.items():
                    if nested_value is not None and nested_value != "":
                        nested_target[nested_key] = str(nested_value)
                continue
            target_key = "deepseek_pro" if section == "models" and key == "deepseek" else key
            target[target_key] = str(value)
    return merged


def redact_config(config: dict) -> dict:
    redacted = {
        key: value.copy() if isinstance(value, dict) else value
        for key, value in config.items()
    }
    redacted["api_keys"] = {
        key: bool(value)
        for key, value in (config.get("api_keys") or {}).items()
    }
    return redacted


def _provider_for(providers: dict, agent: str) -> str:
    defaults = {
        "claude": "claude_cli",
        "codex": "codex_cli",
        "deepseek": "opencode",
        "hermes": "hermes_api",
    }
    return str(providers.get(agent) or defaults.get(agent) or "custom")


def _key_saved(api_keys: dict, agent: str, provider: str) -> bool:
    if api_keys.get(agent):
        return True
    if provider == "openrouter" and api_keys.get("openrouter"):
        return True
    if (agent == "deepseek" or provider == "deepseek_api") and api_keys.get("deepseek"):
        return True
    return False


def build_agent_info(config: dict, session_state: dict | None = None) -> dict:
    effective = overlay_config(config, session_state)
    models = effective.get("models", {})
    binaries = effective.get("binaries", {})
    efforts = effective.get("effort", {})
    roles = effective.get("roles", {})
    providers = effective.get("providers", {})
    api_keys = effective.get("api_keys", {})
    aliases = effective.get("aliases", {})
    model_options = effective.get("model_options", {})
    effort_options = effective.get("effort_options", {})
    return {
        "claude": {
            "label": "Claude",
            "runtime": "claude CLI",
            "provider": _provider_for(providers, "claude"),
            "alias": str(aliases.get("claude") or "claude"),
            "binary": binaries.get("claude") or models.get("claude") or "claude",
            "model": models.get("claude", ""),
            "effort": efforts.get("claude", ""),
            "role": roles.get("claude", ""),
            "model_options": model_options.get("claude", []),
            "effort_options": effort_options.get("claude", []),
            "api_key_saved": _key_saved(api_keys, "claude", _provider_for(providers, "claude")),
            "note": "Uses the Claude CLI default model unless configured there.",
        },
        "codex": {
            "label": "Codex",
            "runtime": "codex exec",
            "provider": _provider_for(providers, "codex"),
            "alias": str(aliases.get("codex") or "codex"),
            "binary": binaries.get("codex") or models.get("codex") or "codex",
            "model": models.get("codex", ""),
            "effort": efforts.get("codex", ""),
            "role": roles.get("codex", ""),
            "model_options": model_options.get("codex", []),
            "effort_options": effort_options.get("codex", []),
            "api_key_saved": _key_saved(api_keys, "codex", _provider_for(providers, "codex")),
            "note": "Uses the Codex CLI default model unless configured there.",
        },
        "deepseek": {
            "label": "Deepseek",
            "runtime": "opencode run",
            "provider": _provider_for(providers, "deepseek"),
            "alias": str(aliases.get("deepseek") or "deepseek"),
            "binary": binaries.get("opencode") or models.get("opencode") or "opencode",
            "model": models.get("deepseek_pro", "deepseek/deepseek-v4-pro"),
            "effort": efforts.get("deepseek", ""),
            "role": roles.get("deepseek", ""),
            "model_options": model_options.get("deepseek", []),
            "effort_options": effort_options.get("deepseek", []),
            "api_key_saved": _key_saved(api_keys, "deepseek", _provider_for(providers, "deepseek")),
            "flash_model": models.get("deepseek_flash", "deepseek/deepseek-v4-flash"),
            "flash_key_saved": bool(api_keys.get("deepseek_flash") or api_keys.get("deepseek")),
            "note": "Configured in Council config.toml or this session.",
        },
        "hermes": {
            "label": "Hermes",
            "runtime": "Hermes API",
            "provider": _provider_for(providers, "hermes"),
            "alias": str(aliases.get("hermes") or "hermes"),
            "binary": "",
            "model": models.get("hermes", "hermes-agent"),
            "effort": efforts.get("hermes", ""),
            "role": roles.get("hermes", ""),
            "model_options": model_options.get("hermes", []),
            "effort_options": effort_options.get("hermes", []),
            "api_key_saved": _key_saved(api_keys, "hermes", _provider_for(providers, "hermes")),
            "note": "Routes through Hermes api_server, usually http://localhost:8642/v1.",
        },
    }


def render_turn(author: str, text: str, timestamp: str | None = None) -> str:
    stamp = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"\n## [@{author}] {stamp}\n{text}\n\n"


def _compaction_prefix_from_chat(chat_path: Path) -> str:
    if not chat_path.exists():
        return ""
    text = chat_path.read_text(encoding="utf-8", errors="replace")
    header_re = re.compile(r"^##\s+\[@(\w+)\]\s+(.+)$", re.MULTILINE)
    matches = list(header_re.finditer(text))
    if not matches:
        return ""
    first = matches[0]
    if first.group(1) != "system" or not first.group(2).startswith("compacted "):
        return ""
    if len(matches) == 1:
        return text.rstrip() + "\n\n"
    return text[:matches[1].start()].rstrip() + "\n\n"


def _load_compaction_summaries(compactions_path: Path | None) -> dict[str, dict]:
    if not compactions_path or not compactions_path.exists():
        return {}
    records: dict[str, dict] = {}
    for line in compactions_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        compaction_id = record.get("id")
        if isinstance(compaction_id, str):
            records[compaction_id] = record
    return records


def _render_compaction_event(event: dict, records: dict[str, dict]) -> str:
    record = records.get(str(event.get("compaction_id"))) or {}
    created_at = str(record.get("created_at") or event.get("ts") or "")
    try:
        compacted_at = datetime.fromisoformat(created_at).strftime("%Y-%m-%d-%H%M%S")
    except ValueError:
        compacted_at = created_at.replace(":", "").replace("T", "-")[:17] or "unknown"
    archive = record.get("summary_path") or "chat-archive/unknown.md"
    summary = str(record.get("summary") or "No summary returned.").rstrip()
    return (
        f"## [@system] compacted {compacted_at}\n"
        f"Compacted previous chat. Archive: `{archive}`\n\n"
        f"{summary}\n\n"
    )


def rebuild_chat_from_events(
    events_path: Path,
    chat_path: Path,
    compactions_path: Path | None = None,
) -> None:
    if not events_path.exists():
        chat_path.write_text("", encoding="utf-8")
        return
    parts: list[str] = []
    compaction_records = _load_compaction_summaries(compactions_path)
    current_compaction_prefix = _compaction_prefix_from_chat(chat_path)
    for line in events_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("kind") == "compaction":
            rendered = current_compaction_prefix or _render_compaction_event(
                event,
                compaction_records,
            )
            parts = [rendered]
            continue
        if event.get("kind") not in ("user_turn", "agent_turn", "system_turn"):
            continue
        author = event.get("author") or "system"
        text = event.get("text") or ""
        parts.append(render_turn(author, text, event.get("display_ts")))
    chat_path.write_text("".join(parts), encoding="utf-8")


def _rewrite_event(events_path: Path, predicate, update) -> bool:
    if not events_path.exists():
        return False
    lines = events_path.read_text(encoding="utf-8", errors="replace").splitlines()
    changed = False
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not predicate(event):
            continue
        updated = update(event)
        lines[i] = json.dumps(updated or event, ensure_ascii=True)
        changed = True
        break
    if changed:
        events_path.write_text(
            "\n".join(lines) + ("\n" if lines else ""),
            encoding="utf-8",
        )
    return changed


def _trace_time(event: dict) -> str:
    ts = str(event.get("ts") or "")
    if not ts:
        return ""
    try:
        return datetime.fromisoformat(ts).strftime("%H:%M:%S")
    except ValueError:
        return ts[-8:] if len(ts) >= 8 else ts


def clean_trace_text(text: str) -> str:
    cleaned = ANSI_RE.sub("", str(text or ""))
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    return "".join(
        ch for ch in cleaned
        if ch == "\n" or ch == "\t" or ord(ch) >= 32
    )


def load_trace_from_events(events_path: Path, limit: int = 100) -> list[dict]:
    if not events_path.exists():
        return []
    trace_events: list[dict] = []
    for line in events_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("kind") != "trace":
            continue
        trace_events.append({
            "time": _trace_time(event),
            "agent": event.get("agent") or "system",
            "message": clean_trace_text(event.get("message") or ""),
            "detail": clean_trace_text(event.get("detail") or ""),
        })
    return trace_events[-limit:]


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
    current_dispatch: Optional[dict] = None
    current_dispatch_task: Optional[asyncio.Task] = None
    active_dispatches: list[dict] = field(default_factory=list)
    active_dispatch_tasks: dict[str, asyncio.Task] = field(default_factory=dict)
    dispatch_queue: list[dict] = field(default_factory=list)
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
            "session_dir": str(self.session_dir),
            "created_at": self.created_at,
            "last_used_at": self.last_used_at,
            "activity": self.activity_summary(),
        }

    def event_line_count(self) -> int:
        if not self.events_path.exists():
            return 0
        return sum(
            1
            for line in self.events_path.read_text(
                encoding="utf-8",
                errors="replace",
            ).splitlines()
            if line.strip()
        )

    def mark_read(self) -> None:
        ui = self.state.setdefault("ui", {})
        ui["read_event_line"] = self.event_line_count()
        self.save_state()

    def unread_finished_count(self) -> int:
        ui = self.state.get("ui") if isinstance(self.state, dict) else {}
        try:
            read_line = int((ui or {}).get("read_event_line") or 0)
        except (TypeError, ValueError):
            read_line = 0
        unread = 0
        if not self.events_path.exists():
            return unread
        for idx, line in enumerate(
            self.events_path.read_text(
                encoding="utf-8",
                errors="replace",
            ).splitlines(),
            start=1,
        ):
            if idx <= read_line or not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("kind") == "agent_turn":
                unread += 1
        return unread

    def activity_summary(self) -> dict:
        running = len(self.active_dispatches)
        if not running and self.current_dispatch:
            running = 1
        queued = len(self.dispatch_queue)
        unread_finished = self.unread_finished_count()
        return {
            "running": running,
            "queued": queued,
            "unread_finished": unread_finished,
            "total_pending": running + queued + unread_finished,
        }

    def compact_status(self) -> dict:
        compact_config = self.effective_config().get("compact", {})
        try:
            threshold = int(compact_config.get("auto_threshold_lines") or 0)
        except (TypeError, ValueError):
            threshold = 0
        try:
            warning_lines = int(compact_config.get("warning_lines_remaining") or 200)
        except (TypeError, ValueError):
            warning_lines = 200

        if self.chat_path.exists():
            text = self.chat_path.read_text(encoding="utf-8", errors="replace")
            line_count = text.count("\n") + (1 if text else 0)
        else:
            line_count = 0

        if threshold <= 0:
            return {
                "enabled": False,
                "line_count": line_count,
                "threshold_lines": threshold,
                "remaining_lines": None,
                "remaining_percent": None,
                "used_percent": None,
                "warning": False,
                "over_threshold": False,
            }

        remaining = threshold - line_count
        remaining_percent = max(0, min(100, round((remaining / threshold) * 100)))
        used_percent = max(0, min(100, round((line_count / threshold) * 100)))
        return {
            "enabled": True,
            "line_count": line_count,
            "threshold_lines": threshold,
            "remaining_lines": max(0, remaining),
            "remaining_percent": remaining_percent,
            "used_percent": used_percent,
            "warning": remaining <= warning_lines,
            "over_threshold": remaining <= 0,
        }

    def effective_config(self) -> dict:
        return overlay_config(self.config, self.state)

    def ensure_files(self) -> None:
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        for agent in AGENTS:
            (self.session_dir / "agent-memory" / agent / "artifacts").mkdir(
                parents=True,
                exist_ok=True,
            )
            (self.session_dir / "agent-memory" / agent / "runs.jsonl").touch(
                exist_ok=True
            )
        self.events_path.touch(exist_ok=True)
        self.trace_events = load_trace_from_events(self.events_path)
        if not self.state_path.exists():
            self.save_state()
        if not self.meta_path.exists():
            self.save_meta()
        if not self.chat_path.exists():
            rebuild_chat_from_events(
                self.events_path,
                self.chat_path,
                self.compactions_path,
            )

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

    def rename(self, new_name: str) -> None:
        self.name = new_name.strip() or self.name
        self.save_meta()

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
        metadata: dict | None = None,
    ) -> str:
        display_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        body = text.rstrip()
        event_kind = kind or ("user_turn" if author == "you" else "agent_turn")
        event: dict = {
            "kind": event_kind,
            "author": author,
            "text": body,
            "display_ts": display_ts,
        }
        if metadata:
            event["metadata"] = metadata
        if usage:
            event["token_usage"] = usage
        pt = event.get("prompt_tokens_est") or (metadata or {}).get("prompt_tokens_est", 0)
        rt = event.get("response_tokens_est") or (metadata or {}).get("response_tokens_est", 0)
        if pt:
            event["prompt_tokens_est"] = pt
        if rt:
            event["response_tokens_est"] = rt
        if not pt and usage and "prompt_tokens" in usage:
            event["prompt_tokens_est"] = usage["prompt_tokens"]
        if not rt and usage and "completion_tokens" in usage:
            event["response_tokens_est"] = usage["completion_tokens"]
        self.append_event(event)
        with open(self.chat_path, "a", encoding="utf-8") as f:
            f.write(render_turn(author, body, display_ts))
        self.touch()
        return f"## [@{author}] {display_ts}"

    def reserve_agent_turn(
        self,
        request_id: str,
        author: str,
        text: str,
        metadata: dict | None = None,
    ) -> str:
        reserve_metadata = {
            "dispatch_request_id": request_id,
            "dispatch_status": "running",
            **(metadata or {}),
        }
        return self.append_turn(author, text, metadata=reserve_metadata)

    def update_reserved_agent_turn(
        self,
        request_id: str,
        text: str | None = None,
        metadata: dict | None = None,
        usage: dict[str, int] | None = None,
        status: str | None = None,
    ) -> bool:
        def matches(event: dict) -> bool:
            meta = event.get("metadata") or {}
            return (
                event.get("kind") == "agent_turn"
                and meta.get("dispatch_request_id") == request_id
            )

        def update(event: dict) -> dict:
            meta = event.setdefault("metadata", {})
            if text is not None:
                event["text"] = text.rstrip()
            if metadata:
                meta.update(metadata)
            if status:
                meta["dispatch_status"] = status
            if usage:
                event["token_usage"] = usage
            pt = meta.get("prompt_tokens_est")
            rt = meta.get("response_tokens_est")
            if pt:
                event["prompt_tokens_est"] = pt
            if rt:
                event["response_tokens_est"] = rt
            if usage and not pt and "prompt_tokens" in usage:
                event["prompt_tokens_est"] = usage["prompt_tokens"]
            if usage and not rt and "completion_tokens" in usage:
                event["response_tokens_est"] = usage["completion_tokens"]
            return event

        changed = _rewrite_event(self.events_path, matches, update)
        if changed:
            rebuild_chat_from_events(
                self.events_path,
                self.chat_path,
                self.compactions_path,
            )
            self.touch()
        return changed

    async def broadcast_status(self):
        await self.broadcast({
            "type": "status",
            "busy": self.busy,
            "agent": self.current_agent,
            "current_agent": self.current_agent,
            "current_dispatch": self.current_dispatch,
            "active_dispatches": self.active_dispatches_snapshot(),
            "dispatch_queue": self.dispatch_queue_snapshot(),
            "project": self.project_name,
            "session_id": self.id,
            "agents": build_agent_info(self.config, self.state),
            "compact": self.compact_status(),
        })

    def active_dispatches_snapshot(self) -> list[dict]:
        return [item.copy() for item in self.active_dispatches]

    def dispatch_queue_snapshot(self) -> list[dict]:
        return [item.copy() for item in self.dispatch_queue]

    async def set_dispatch_queue(self, queue: list[dict]) -> None:
        self.dispatch_queue = queue
        await self.broadcast_status()

    async def set_current_dispatch(self, request: dict | None) -> None:
        self.current_dispatch = request.copy() if request else None
        await self.broadcast_status()

    async def start_dispatch(self, request: dict, task: asyncio.Task) -> None:
        request_id = request.get("id")
        if not request_id:
            return
        clean = request.copy()
        self.active_dispatches = [
            item for item in self.active_dispatches
            if item.get("id") != request_id
        ]
        self.active_dispatches.append(clean)
        self.active_dispatch_tasks[request_id] = task
        self.current_dispatch = self.active_dispatches[0] if self.active_dispatches else None
        if len(self.active_dispatches) == 1:
            self.current_agent = clean.get("agent")
        elif self.active_dispatches:
            self.current_agent = "multiple"
        self.busy = True
        await self.broadcast_status()

    async def finish_dispatch(self, request_id: str) -> None:
        self.active_dispatch_tasks.pop(request_id, None)
        self.active_dispatches = [
            item for item in self.active_dispatches
            if item.get("id") != request_id
        ]
        self.current_dispatch = self.active_dispatches[0] if self.active_dispatches else None
        if len(self.active_dispatches) == 1:
            self.current_agent = self.active_dispatches[0].get("agent")
        elif self.active_dispatches:
            self.current_agent = "multiple"
        else:
            self.current_agent = None
            self.busy = False
        await self.broadcast_status()

    async def cancel_dispatch_request(self, request_id: str) -> bool:
        task = self.active_dispatch_tasks.get(request_id)
        if task is not None and not task.done():
            agent = next(
                (
                    item.get("agent") or "system"
                    for item in self.active_dispatches
                    if item.get("id") == request_id
                ),
                "system",
            )
            await self.add_trace(agent, "cancel requested", "Stopping active agent subprocess.")
            task.cancel()
            return True
        if self.current_dispatch and self.current_dispatch.get("id") == request_id:
            return await self.cancel_current_dispatch()
        for i, item in enumerate(self.dispatch_queue):
            if item.get("id") == request_id:
                removed = self.dispatch_queue.pop(i)
                await self.add_trace(
                    removed.get("agent") or "system",
                    "queued dispatch cancelled",
                    "Removed from pending dispatch queue.",
                )
                await self.broadcast_status()
                return True
        return False

    async def set_busy(self, agent: str):
        self.busy = True
        self.current_agent = agent
        await self.broadcast_status()

    async def set_idle(self):
        if self.active_dispatches:
            self.busy = True
            self.current_dispatch = self.active_dispatches[0]
            self.current_agent = (
                self.active_dispatches[0].get("agent")
                if len(self.active_dispatches) == 1 else "multiple"
            )
            await self.broadcast_status()
            return
        self.busy = False
        self.current_agent = None
        self.current_dispatch = None
        await self.broadcast_status()

    async def set_dispatch_task(self, task: asyncio.Task | None):
        self.current_dispatch_task = task

    async def cancel_current_dispatch(self) -> bool:
        if self.current_dispatch:
            task = self.active_dispatch_tasks.get(self.current_dispatch.get("id"))
            if task is not None and not task.done():
                agent = self.current_dispatch.get("agent") or "system"
                await self.add_trace(agent, "cancel requested", "Stopping active agent subprocess.")
                task.cancel()
                return True
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
            "message": clean_trace_text(message),
            "detail": clean_trace_text(detail),
        }
        self.trace_events.append(event)
        self.trace_events = self.trace_events[-100:]
        self.append_event({
            "kind": "trace",
            "agent": agent,
            "message": event["message"],
            "detail": event["detail"],
        })
        await self.broadcast({"type": "trace_update", "session_id": self.id, "event": event})

    async def update_config(self, changes: dict) -> None:
        for section in STATE_SECTIONS:
            section_changes = changes.get(section)
            if not isinstance(section_changes, dict):
                continue
            target = self.state.setdefault(section, {})
            for key, value in section_changes.items():
                if section in ("models", "api_keys") and key == "deepseek_flash":
                    target[key] = str(value)
                    continue
                if section == "api_keys" and key == "openrouter":
                    target[key] = str(value)
                    continue
                if section == "dispatch":
                    if isinstance(value, dict):
                        nested_target = target.setdefault(key, {})
                        if not isinstance(nested_target, dict):
                            nested_target = {}
                            target[key] = nested_target
                        for nested_key, nested_value in value.items():
                            nested_target[nested_key] = str(nested_value)
                        continue
                    target[key] = str(value)
                    continue
                if section == "aliases":
                    if key not in AGENTS:
                        continue
                    alias = str(value).strip().lstrip("@").lower()
                    target[key] = alias
                    continue
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
        self.default_project_root = normalize_project_root(default_project_root)
        self.sessions_dir = self._project_sessions_dir(default_project_root)
        self.legacy_sessions_dir = council_root / "sessions"
        self.registry_path = self.sessions_dir / "sessions.json"
        self.config = config
        self.config_path = config_path
        self.secrets_path = council_root / ".council" / "secrets.json"
        secrets = _json_default(self.secrets_path, {"api_keys": {}})
        if isinstance(secrets.get("api_keys"), dict):
            self.config.setdefault("api_keys", {}).update(secrets["api_keys"])
        self.sessions: dict[str, Session] = {}
        self.active_session_id: str | None = None

    def _project_sessions_dir(self, project_root: Path) -> Path:
        try:
            root = normalize_project_root(project_root)
        except Exception:
            logger.exception("Failed to resolve project root %s", project_root)
            return self.council_root / "sessions"
        return root / ".council" / "sessions"

    def load_all(self) -> None:
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self._migrate_legacy_project_sessions()
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

    def _migrate_legacy_project_sessions(self) -> None:
        if self.legacy_sessions_dir == self.sessions_dir:
            return

        legacy_registry_path = self.legacy_sessions_dir / "sessions.json"
        if not legacy_registry_path.exists():
            return

        legacy_registry = _json_default(
            legacy_registry_path,
            {"sessions": [], "active_session": None},
        )
        local_registry = _json_default(
            self.registry_path,
            {"sessions": [], "active_session": None},
        )
        local_sessions = local_registry.setdefault("sessions", [])
        local_by_id = {
            str(meta.get("id")): meta
            for meta in local_sessions
            if isinstance(meta, dict) and meta.get("id")
        }

        changed = False
        migrated_active = None
        for meta in legacy_registry.get("sessions", []):
            if not isinstance(meta, dict) or not meta.get("id"):
                continue
            try:
                project_root = normalize_project_root(
                    meta.get("project_root") or self.default_project_root
                )
            except Exception:
                continue
            if project_root != self.default_project_root:
                continue

            session_id = str(meta["id"])
            src = self.legacy_sessions_dir / session_id
            dst = self.sessions_dir / session_id
            local_meta = local_by_id.get(session_id)
            legacy_last_used = str(meta.get("last_used_at") or "")
            local_last_used = str(local_meta.get("last_used_at") or "") if local_meta else ""
            if src.exists() and not dst.exists():
                shutil.copytree(src, dst)
                changed = True
            elif src.exists() and legacy_last_used > local_last_used:
                shutil.copytree(src, dst, dirs_exist_ok=True)
                changed = True

            if local_meta is None:
                local_sessions.append(meta)
                local_by_id[session_id] = meta
                changed = True
            elif legacy_last_used > local_last_used:
                local_meta.clear()
                local_meta.update(meta)
                changed = True
            if legacy_registry.get("active_session") == session_id:
                migrated_active = session_id

        if migrated_active and local_registry.get("active_session") != migrated_active:
            local_registry["active_session"] = migrated_active
            changed = True

        if changed:
            self.registry_path.write_text(
                json.dumps(local_registry, indent=2) + "\n",
                encoding="utf-8",
            )

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
        touched_secrets = False
        for section in STATE_SECTIONS:
            section_changes = changes.get(section)
            if not isinstance(section_changes, dict):
                continue
            target = self.config.setdefault(section, {})
            if section in SENSITIVE_SECTIONS:
                touched_secrets = True
            for key, value in section_changes.items():
                if section in ("models", "api_keys") and key == "deepseek_flash":
                    target[key] = str(value)
                    continue
                if section == "api_keys" and key == "openrouter":
                    target[key] = str(value)
                    continue
                if section == "dispatch":
                    if isinstance(value, dict):
                        nested_target = target.setdefault(key, {})
                        if not isinstance(nested_target, dict):
                            nested_target = {}
                            target[key] = nested_target
                        for nested_key, nested_value in value.items():
                            nested_target[nested_key] = str(nested_value)
                        continue
                    target[key] = str(value)
                    continue
                if section == "aliases":
                    if key not in AGENTS:
                        continue
                    alias = str(value).strip().lstrip("@").lower()
                    target[key] = alias
                    continue
                if key not in AGENTS:
                    continue
                target_key = "deepseek_pro" if section == "models" and key == "deepseek" else key
                target[target_key] = str(value)
        if self.config_path is not None:
            write_config(self.config_path, self.config)
        if touched_secrets:
            self.secrets_path.parent.mkdir(parents=True, exist_ok=True)
            self.secrets_path.write_text(
                json.dumps(
                    {"api_keys": self.config.get("api_keys", {})},
                    indent=2,
                ) + "\n",
                encoding="utf-8",
            )
        for session in self.sessions.values():
            await session.add_trace("system", "global config updated", ", ".join(changes.keys()))
            await session.broadcast_status()
