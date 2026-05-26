"""FastAPI app: multi-session routes, websocket, and static file serving."""

import asyncio
import base64
import binascii
import json
import logging
import mimetypes
import os
import re
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from starlette.websockets import WebSocketState

from .state import AGENTS, Session, SessionRegistry, _active_agents, build_agent_info, rebuild_chat_from_events, runtime_family_for_provider
from .daemon import session_daemon_loop
from .summarizer import compact_chat
from .completions import list_agents, list_project_files
from .agent_memory import read_agent_memory_for_ui
from .prompt_builder import preview_prompt

logger = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
TURN_KINDS = {"user_turn", "agent_turn", "system_turn"}
CONTEXT_MAX_CHARS = 16000
CONTEXT_DEFAULT_CHARS = 8000
IMAGE_UPLOAD_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
}
MAX_IMAGE_UPLOAD_BYTES = 10 * 1024 * 1024


def _session_agents_md_path(session: Session) -> Path:
    return session.working_root / "AGENTS.md"


def _read_session_agents_doc(session: Session) -> dict:
    agents_path = _session_agents_md_path(session)
    claude_path = session.working_root / "CLAUDE.md"
    if agents_path.exists():
        return {
            "path": str(agents_path),
            "filename": "AGENTS.md",
            "exists": True,
            "fallback": False,
            "text": agents_path.read_text(encoding="utf-8", errors="replace"),
        }
    if claude_path.exists():
        return {
            "path": str(agents_path),
            "filename": "AGENTS.md",
            "exists": False,
            "fallback": True,
            "fallback_filename": "CLAUDE.md",
            "fallback_path": str(claude_path),
            "text": claude_path.read_text(encoding="utf-8", errors="replace"),
        }
    return {
        "path": str(agents_path),
        "filename": "AGENTS.md",
        "exists": False,
        "fallback": False,
        "text": "",
    }


def _write_session_agents_doc(session: Session, text: str) -> None:
    path = _session_agents_md_path(session)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + ("\n" if text.strip() else ""), encoding="utf-8")


def _get_session(registry: SessionRegistry, session_id: str) -> Session:
    try:
        return registry.get(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="session not found")


def _turn_memory_snapshot(session: Session, author: str, max_chars: int = 20000) -> dict | None:
    agents = session._session_agents()
    if author not in agents:
        return None
    return read_agent_memory_for_ui(session.session_dir, author, max_chars=max_chars, agents=agents)


def _allowed_config_changes(body: dict) -> dict:
    allowed_sections = {
        "models",
        "effort",
        "roles",
        "dispatch",
        "providers",
        "api_keys",
        "aliases",
        "ui",
    }
    return {
        section: value
        for section, value in body.items()
        if section in allowed_sections and isinstance(value, dict)
    }


def _split_sensitive_config_changes(changes: dict) -> tuple[dict, dict]:
    sensitive: dict = {}
    safe: dict = {}
    for section, value in changes.items():
        if section == "api_keys":
            sensitive[section] = value
        else:
            safe[section] = value
    return safe, sensitive


def _load_event_lines(session: Session) -> tuple[list[str], list[tuple[int, dict]]]:
    lines = session.events_path.read_text(encoding="utf-8").splitlines()
    turns: list[tuple[int, dict]] = []
    last_compaction_idx = -1
    for line_idx, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("kind") == "compaction":
            last_compaction_idx = line_idx
            turns.clear()
            continue
        if event.get("kind") in TURN_KINDS and line_idx > last_compaction_idx:
            turns.append((line_idx, event))
    return lines, turns


def _find_turn_line(body: dict, turns: list[tuple[int, dict]]) -> int | None:
    event_line_idx = body.get("event_line_idx")
    author = body.get("author")
    display_ts = body.get("display_ts")
    original_text = body.get("original_text")

    if isinstance(event_line_idx, int):
        for line_idx, event in turns:
            if line_idx != event_line_idx:
                continue
            return line_idx

    if isinstance(author, str) and isinstance(display_ts, str) and isinstance(original_text, str):
        matches: list[int] = []
        for line_idx, event in turns:
            if (
                event.get("author") == author
                and event.get("display_ts") == display_ts
                and (event.get("text") or "").rstrip() == original_text.rstrip()
            ):
                matches.append(line_idx)
        if not matches:
            return None
        turn_index = body.get("index")
        if isinstance(turn_index, int) and 0 <= turn_index < len(turns):
            indexed_line_idx = turns[turn_index][0]
            if indexed_line_idx in matches:
                return indexed_line_idx
        if len(matches) == 1:
            return matches[0]
        return None

    turn_index = body.get("index")
    if isinstance(turn_index, int) and 0 <= turn_index < len(turns):
        return turns[turn_index][0]
    return None


def _turn_header(event: dict) -> str:
    author = event.get("author") or "system"
    display_ts = event.get("display_ts") or ""
    return f"## [@{author}] {display_ts}"


def _compaction_display_ts(event: dict) -> str:
    created_at = str(event.get("ts") or "")
    if created_at:
        try:
            from datetime import datetime

            dt = datetime.fromisoformat(created_at)
            return "compacted " + dt.strftime("%Y-%m-%d-%H%M%S")
        except ValueError:
            return "compacted " + created_at
    return "compacted summary"


def _active_dispatches_for_header(session: Session, header: str) -> list[dict]:
    return [
        item.copy()
        for item in session.active_dispatches_snapshot()
        if item.get("header") == header
    ]


def _last_agent_speaker(session: Session) -> str | None:
    turns = _context_turns(session)
    if not turns:
        return None
    agents = session._session_agents()
    for _, event in reversed(turns):
        author = event.get("author")
        if author in agents:
            return str(author)
    return None
    try:
        _, turns = _load_event_lines(session)
    except Exception:
        logger.exception("Failed to resolve last agent speaker for %s", session.id)
        return None
    for _, event in reversed(turns):
        author = event.get("author")
        if author in AGENTS:
            return str(author)
    return None


def _clip_context_text(text: str, max_chars: int) -> str:
    max_chars = max(1000, min(int(max_chars or CONTEXT_DEFAULT_CHARS), CONTEXT_MAX_CHARS))
    if len(text) <= max_chars:
        return text
    marker = "\n\n...[context truncated]...\n\n"
    keep = max_chars - len(marker)
    head = max(0, keep // 2)
    tail = max(0, keep - head)
    return text[:head].rstrip() + marker + text[-tail:].lstrip()


def _latest_context_summary(session: Session) -> str:
    latest: dict | None = None
    if session.compactions_path.exists():
        for line in session.compactions_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                latest = record
    if latest:
        archive = latest.get("summary_path") or "chat-archive/unknown.md"
        created_at = latest.get("created_at") or "unknown time"
        body = str(latest.get("summary") or "").strip()
        if body:
            return f"Compacted at {created_at}. Archive: `{archive}`\n\n{body}"
    return "(no compacted summary available)"


def _context_turns(session: Session) -> list[tuple[int, dict]]:
    if not session.events_path.exists():
        return []
    _, turns = _load_event_lines(session)
    return turns


def _context_agents(turns: list[tuple[int, dict]], session: Session | None = None) -> list[str]:
    agents = session._session_agents() if session else list(AGENTS)
    return sorted({
        str(event.get("author"))
        for _, event in turns
        if event.get("author") in agents
    })


def _format_context_turn(turn_number: int, event: dict) -> str:
    author = str(event.get("author") or "system")
    display_ts = str(event.get("display_ts") or "")
    text = str(event.get("text") or "").rstrip()
    return f"## turn:{turn_number} [@{author}] {display_ts}\n{text}".rstrip()


def _resolve_context_author(raw: str, session: Session) -> str:
    wanted = raw.strip().lstrip("@").lower()
    aliases = session.effective_config().get("aliases", {})
    agents = session._session_agents()
    for canonical in (*agents, "you"):
        alias = str(aliases.get(canonical) or canonical).strip().lstrip("@").lower()
        if wanted in {canonical, alias}:
            return canonical
    return wanted


def _slice_session_context(session: Session, raw_slice: str, max_chars: int) -> dict:
    slice_spec = (raw_slice or "summary").strip()
    turns = _context_turns(session)

    if slice_spec == "summary":
        text = _latest_context_summary(session)
    elif slice_spec.startswith("tail:"):
        try:
            count = max(1, min(int(slice_spec.split(":", 1)[1]), 50))
        except ValueError:
            raise HTTPException(status_code=400, detail="tail slice must be tail:N")
        start = max(0, len(turns) - count)
        text = "\n\n".join(
            _format_context_turn(idx + 1, event)
            for idx, (_, event) in enumerate(turns[start:], start=start)
        ) or "(no turns available)"
    elif slice_spec.startswith("search:"):
        query = slice_spec.split(":", 1)[1].strip().lower()
        if not query:
            raise HTTPException(status_code=400, detail="search slice requires a query")
        matches = [
            (idx, event)
            for idx, (_, event) in enumerate(turns, start=1)
            if query in str(event.get("text") or "").lower()
            or query in str(event.get("author") or "").lower()
            or query in str(event.get("display_ts") or "").lower()
        ]
        text = "\n\n".join(_format_context_turn(idx, event) for idx, event in matches[-20:])
        if not text:
            text = f"(no matches for {query!r})"
    elif slice_spec.startswith("turn:"):
        _, rest = slice_spec.split(":", 1)
        author_raw, sep, selector = rest.rpartition(":")
        if not sep or not author_raw:
            raise HTTPException(status_code=400, detail="turn slice must be turn:@agent:last or turn:@agent:N")
        author = _resolve_context_author(author_raw, session)
        author_turns = [
            (idx, event)
            for idx, (_, event) in enumerate(turns, start=1)
            if event.get("author") == author
        ]
        if selector == "last":
            offset = 1
        else:
            try:
                offset = max(1, int(selector))
            except ValueError:
                raise HTTPException(status_code=400, detail="turn selector must be last or a positive integer")
        if offset > len(author_turns):
            text = f"(no turn {selector!r} for @{author})"
        else:
            idx, event = author_turns[-offset]
            text = _format_context_turn(idx, event)
    elif slice_spec.startswith("range:"):
        body = slice_spec.split(":", 1)[1].strip()
        if ".." not in body:
            raise HTTPException(status_code=400, detail="range slice must be range:A..B")
        start_raw, end_raw = body.split("..", 1)
        try:
            start = max(1, int(start_raw))
            end = max(start, int(end_raw))
        except ValueError:
            raise HTTPException(status_code=400, detail="range bounds must be turn numbers")
        selected = [
            (idx, event)
            for idx, (_, event) in enumerate(turns, start=1)
            if start <= idx <= end
        ]
        text = "\n\n".join(_format_context_turn(idx, event) for idx, event in selected)
        if not text:
            text = f"(no turns in range {start}..{end})"
    else:
        raise HTTPException(
            status_code=400,
            detail="unsupported slice; use summary, tail:N, search:QUERY, turn:@agent:last, turn:@agent:N, or range:A..B",
        )

    clipped = _clip_context_text(text, max_chars)
    return {
        "session_id": session.id,
        "slice": slice_spec,
        "max_chars": max(1000, min(int(max_chars or CONTEXT_DEFAULT_CHARS), CONTEXT_MAX_CHARS)),
        "truncated": len(text) > len(clipped),
        "turn_count": len(turns),
        "agents": _context_agents(turns, session=session),
        "text": clipped,
    }


def _require_local_context_request(request: Request) -> None:
    host = request.client.host if request.client else ""
    if host in {"127.0.0.1", "::1", "localhost", "testclient"}:
        return
    raise HTTPException(status_code=403, detail="context fetch is only available from localhost")


def _resolve_quick_reply(text: str, session: Session) -> str:
    stripped = text.strip()
    if stripped != "@@" and not stripped.startswith("@@ "):
        return text
    agent = _last_agent_speaker(session)
    if not agent:
        return text
    aliases = session.effective_config().get("aliases", {})
    alias = str(aliases.get(agent) or agent).strip().lstrip("@") or agent
    replacement = f"@{alias}"
    if stripped == "@@":
        return replacement
    return replacement + stripped[2:]


def _decode_image_upload(body: dict) -> tuple[bytes, str]:
    media_type = str(body.get("media_type") or "").strip().lower()
    data = str(body.get("data") or "").strip()
    if not data:
        raise ValueError("image data is required")
    if data.startswith("data:"):
        header, sep, payload = data.partition(",")
        if not sep or ";base64" not in header:
            raise ValueError("image data must be base64")
        media_type = header[5:].split(";", 1)[0].strip().lower()
        data = payload
    if media_type not in IMAGE_UPLOAD_TYPES:
        raise ValueError("unsupported image type")
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("invalid base64 image data")
    if not raw:
        raise ValueError("image data is empty")
    if len(raw) > MAX_IMAGE_UPLOAD_BYTES:
        raise ValueError("image is larger than 10MB")
    return raw, media_type


def _new_attachment_name(media_type: str) -> str:
    from datetime import datetime

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    token = os.urandom(4).hex()
    return f"paste-{stamp}-{token}{IMAGE_UPLOAD_TYPES[media_type]}"


async def _stop_task(task: asyncio.Task | None) -> None:
    if task is None or task.done():
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def _start_session_daemon(app: FastAPI, session: Session) -> None:
    tasks: dict[str, asyncio.Task] = app.state.council_daemons
    if session.id in tasks and not tasks[session.id].done():
        return
    tasks[session.id] = asyncio.create_task(
        session_daemon_loop(session, app.state.council_registry)
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    registry: SessionRegistry = app.state.council_registry
    app.state.council_daemons = {}
    registry.add_global_log("Council server started", f"sessions={len(registry.sessions)}")
    for session in registry.sessions.values():
        _start_session_daemon(app, session)
    yield
    tasks = list(app.state.council_daemons.values())
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def create_app(registry: SessionRegistry) -> FastAPI:
    app = FastAPI(lifespan=lifespan)
    app.state.council_registry = registry
    app.state.council_daemons = {}

    @app.get("/")
    async def get_index():
        index_path = FRONTEND_DIR / "index.html"
        return HTMLResponse(index_path.read_text(encoding="utf-8"))

    @app.get("/app.js")
    async def get_app_js():
        return FileResponse(FRONTEND_DIR / "app.js", media_type="application/javascript")

    @app.get("/style.css")
    async def get_style_css():
        return FileResponse(FRONTEND_DIR / "style.css", media_type="text/css")

    @app.get("/icons/{name}")
    async def get_icon(name: str):
        icon_path = FRONTEND_DIR / "icons" / name
        if not icon_path.is_file():
            raise HTTPException(status_code=404)
        mt = "image/png"
        return FileResponse(icon_path, media_type=mt)

    @app.get("/api/sessions")
    async def get_sessions():
        return {
            "sessions": registry.list(),
            "active_session_id": registry.active_session_id,
        }

    @app.get("/api/global-log")
    async def get_global_log():
        return {"events": registry.global_log()}

    @app.post("/api/sessions")
    async def post_session(body: dict):
        name = str(body.get("name") or "").strip()
        project_root = str(body.get("project_root") or "").strip()
        session = registry.create_session(name or "untitled", project_root or None, activate=True)
        _start_session_daemon(app, session)
        return {
            "session": session.metadata(),
            "active_session_id": registry.active_session_id,
        }

    @app.delete("/api/sessions/{session_id}")
    async def delete_session(session_id: str):
        _get_session(registry, session_id)
        if len(registry.sessions) <= 1:
            return JSONResponse({"error": "cannot delete the last session"}, status_code=409)
        task = app.state.council_daemons.pop(session_id, None)
        await _stop_task(task)
        registry.delete_session(session_id)
        return {
            "ok": True,
            "sessions": registry.list(),
            "active_session_id": registry.active_session_id,
        }

    @app.post("/api/sessions/{session_id}/activate")
    async def post_activate(session_id: str):
        session = registry.set_active(session_id)
        return {
            "ok": True,
            "session": session.metadata(),
            "active_session_id": registry.active_session_id,
        }

    @app.patch("/api/sessions/{session_id}")
    async def patch_session_rename(session_id: str, body: dict):
        session = _get_session(registry, session_id)
        new_name = str(body.get("name") or "").strip()
        if not new_name:
            return JSONResponse({"error": "name is required"}, status_code=400)
        session.rename(new_name)
        await session.broadcast_status()
        return {"ok": True, "session": session.metadata()}

    @app.get("/api/sessions/{session_id}/chat")
    async def get_chat(session_id: str):
        session = _get_session(registry, session_id)
        session.mark_read()
        if session.chat_path.exists():
            return {"text": session.chat_path.read_text(encoding="utf-8", errors="replace")}
        return {"text": ""}

    @app.get("/api/sessions/{session_id}/context")
    async def get_session_context(
        session_id: str,
        request: Request,
        slice: str = "summary",
        max_chars: int = CONTEXT_DEFAULT_CHARS,
    ):
        _require_local_context_request(request)
        session = _get_session(registry, session_id)
        return _slice_session_context(session, slice, max_chars)

    @app.get("/api/sessions/{session_id}/agents-md")
    async def get_agents_md(session_id: str):
        session = _get_session(registry, session_id)
        return _read_session_agents_doc(session)

    @app.put("/api/sessions/{session_id}/agents-md")
    async def put_agents_md(session_id: str, body: dict):
        session = _get_session(registry, session_id)
        text = body.get("text")
        if not isinstance(text, str):
            return JSONResponse({"error": "text is required"}, status_code=400)
        _write_session_agents_doc(session, text)
        await session.add_trace("system", "AGENTS.md updated", str(_session_agents_md_path(session)))
        return {"ok": True, **_read_session_agents_doc(session)}

    @app.post("/api/sessions/{session_id}/send")
    async def post_send(session_id: str, body: dict):
        session = _get_session(registry, session_id)
        if session.compacting:
            return JSONResponse(
                {"error": "cannot send messages while Council is compacting"},
                status_code=409,
            )
        text = str(body.get("text", ""))
        if not text.strip():
            return JSONResponse({"error": "empty message"}, status_code=400)
        text = _resolve_quick_reply(text, session)
        dispatch_mode = str(body.get("dispatch_mode") or "parallel").lower()
        if dispatch_mode not in ("parallel", "queued"):
            return JSONResponse({"error": "invalid dispatch_mode"}, status_code=400)
        session.append_turn("you", text, metadata={"dispatch_mode": dispatch_mode})
        await session.notify_chat_update()
        return {"ok": True}

    @app.post("/api/sessions/{session_id}/promote_turn")
    async def post_promote_turn(session_id: str, body: dict):
        session = _get_session(registry, session_id)
        lines, turns = _load_event_lines(session)
        target_idx = _find_turn_line(body, turns)
        if target_idx is None:
            return JSONResponse({"error": "turn not found"}, status_code=404)
        event = json.loads(lines[target_idx])
        if event.get("author") != "you":
            return JSONResponse({"error": "only your own queued turns can be promoted"}, status_code=403)
        meta = event.setdefault("metadata", {})
        if meta.get("dispatch_mode") != "queued":
            return JSONResponse({"error": "turn is not queued"}, status_code=409)
        header = f"## [@you] {event.get('display_ts') or ''}"
        promoted = await session.promote_queued_dispatches_for_header(header)
        if not promoted:
            return JSONResponse({"error": "no queued dispatches for this turn"}, status_code=409)
        meta["dispatch_mode"] = "parallel"
        lines[target_idx] = json.dumps(event, ensure_ascii=True)
        session.events_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        await session.add_trace("system", "queued turn promoted", header)
        await session.notify_chat_update()
        return {"ok": True, "promoted": promoted}

    @app.post("/api/sessions/{session_id}/compact")
    async def post_compact(session_id: str):
        session = _get_session(registry, session_id)
        if session.busy or session.active_dispatches or session.dispatch_queue:
            return JSONResponse(
                {
                    "error": "cannot compact while Council is busy or agents are queued",
                    "active_dispatches": session.active_dispatches_snapshot(),
                    "dispatch_queue": session.dispatch_queue_snapshot(),
                },
                status_code=409,
            )
        await session.set_compacting()
        try:
            await compact_chat(session, session.effective_config())
        finally:
            await session.set_idle()
        await session.notify_chat_update()
        return {"ok": True}

    @app.post("/api/sessions/{session_id}/erase")
    async def post_erase(session_id: str):
        session = _get_session(registry, session_id)
        cancelled_tasks = await session.cancel_all_dispatches()
        if cancelled_tasks:
            await asyncio.gather(*cancelled_tasks, return_exceptions=True)
        archive = session.hard_clear_context()
        await session.add_trace(
            "system",
            "session context cleared",
            f"Archived prior context under {archive['archive_dir']}.",
        )
        await session.notify_chat_update()
        await session.broadcast_status()
        return {"ok": True, **archive}

    @app.post("/api/sessions/{session_id}/erase_turn")
    async def post_erase_turn(session_id: str, body: dict):
        session = _get_session(registry, session_id)
        turn_index = body.get("index")
        if not isinstance(turn_index, int) or turn_index < 0:
            return JSONResponse({"error": "invalid turn index"}, status_code=400)
        lines, turns = _load_event_lines(session)
        target_idx = _find_turn_line(body, turns)
        if target_idx is None:
            return JSONResponse({"error": "turn not found"}, status_code=404)
        event = json.loads(lines[target_idx])
        header = _turn_header(event)
        active = _active_dispatches_for_header(session, header)
        if active:
            return JSONResponse(
                {
                    "error": "cannot erase a turn while its agent dispatch is running",
                    "active_dispatches": active,
                },
                status_code=409,
            )
        removed_dispatches = await session.remove_queued_dispatches_for_header(header)
        old_chat = session.chat_path.read_text(encoding="utf-8", errors="replace")
        del lines[target_idx]
        session.events_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        rebuild_chat_from_events(
            session.events_path,
            session.chat_path,
            session.compactions_path,
        )
        new_chat = session.chat_path.read_text(encoding="utf-8", errors="replace")
        if old_chat == new_chat:
            return JSONResponse({"error": "nothing changed — turn may already be archived"}, status_code=409)
        await session.add_trace("system", f"turn {turn_index} erased")
        if removed_dispatches:
            await session.add_trace(
                "system",
                "queued dispatches cancelled",
                f"Removed {len(removed_dispatches)} queued dispatch(es) for erased turn.",
            )
        await session.notify_chat_update()
        return {"ok": True, "cancelled_dispatches": len(removed_dispatches)}

    @app.post("/api/sessions/{session_id}/reset_to_turn")
    async def post_reset_to_turn(session_id: str, body: dict):
        session = _get_session(registry, session_id)
        if session.busy or session.active_dispatches or session.dispatch_queue:
            return JSONResponse(
                {
                    "error": "cannot reset discussion while agents are running or queued",
                    "active_dispatches": session.active_dispatches_snapshot(),
                    "dispatch_queue": session.dispatch_queue_snapshot(),
                },
                status_code=409,
            )
        lines, turns = _load_event_lines(session)
        target_idx = _find_turn_line(body, turns)
        if target_idx is None and isinstance(body.get("event_line_idx"), int):
            candidate_idx = body["event_line_idx"]
            if 0 <= candidate_idx < len(lines):
                try:
                    candidate = json.loads(lines[candidate_idx])
                except json.JSONDecodeError:
                    candidate = {}
                if candidate.get("kind") == "compaction":
                    target_idx = candidate_idx
        if target_idx is None:
            return JSONResponse({"error": "turn not found or already archived"}, status_code=404)
        archive = session.reset_to_event_line(target_idx)
        await session.add_trace(
            "system",
            "discussion reset",
            f"Reset to turn {body.get('index')}; archived discarded tail under {archive['archive_dir']}.",
        )
        await session.notify_chat_update()
        await session.broadcast_status()
        return {"ok": True, **archive}

    @app.post("/api/sessions/{session_id}/edit_turn")
    async def post_edit_turn(session_id: str, body: dict):
        session = _get_session(registry, session_id)
        turn_index = body.get("index")
        new_text = body.get("text")
        if not isinstance(turn_index, int) or turn_index < 0:
            return JSONResponse({"error": "invalid turn index"}, status_code=400)
        if not isinstance(new_text, str):
            return JSONResponse({"error": "text is required"}, status_code=400)
        lines, turns = _load_event_lines(session)
        target_idx = _find_turn_line(body, turns)
        if target_idx is None:
            return JSONResponse({"error": "turn not found"}, status_code=404)
        event = json.loads(lines[target_idx])
        if event.get("author") != "you":
            return JSONResponse({"error": "only your own turns can be edited"}, status_code=403)
        old_chat = session.chat_path.read_text(encoding="utf-8", errors="replace")
        event["text"] = new_text.rstrip()
        lines[target_idx] = json.dumps(event, ensure_ascii=True)
        session.events_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        rebuild_chat_from_events(
            session.events_path,
            session.chat_path,
            session.compactions_path,
        )
        new_chat = session.chat_path.read_text(encoding="utf-8", errors="replace")
        if old_chat == new_chat:
            return JSONResponse({"error": "nothing changed — turn may already be archived"}, status_code=409)
        await session.add_trace("system", f"turn {turn_index} edited")
        await session.notify_chat_update()
        return {"ok": True}

    @app.post("/api/sessions/{session_id}/cancel")
    async def post_cancel(session_id: str):
        session = _get_session(registry, session_id)
        cancelled = await session.cancel_current_dispatch()
        if not cancelled:
            return JSONResponse({"error": "no active dispatch"}, status_code=409)
        return {"ok": True}

    @app.post("/api/sessions/{session_id}/dispatch/{request_id}/cancel")
    async def post_cancel_dispatch_request(session_id: str, request_id: str):
        session = _get_session(registry, session_id)
        cancelled = await session.cancel_dispatch_request(request_id)
        if not cancelled:
            return JSONResponse({"error": "dispatch request not found"}, status_code=404)
        return {"ok": True}

    @app.post("/api/sessions/{session_id}/restart")
    async def post_restart(session_id: str):
        session = _get_session(registry, session_id)
        await session.add_trace("system", "restart requested")
        registry.add_global_log("Council restart requested", f"session={session.name} ({session.id})")

        async def _delayed_exit():
            await asyncio.sleep(0.5)
            os._exit(42)

        asyncio.create_task(_delayed_exit())
        return {"ok": True}

    @app.post("/api/sessions/{session_id}/open-file")
    async def post_open_file(session_id: str, body: dict):
        session = _get_session(registry, session_id)
        root = session.working_root
        rel_path = str(body.get("path") or "").strip()
        if not rel_path:
            return JSONResponse({"error": "path is required"}, status_code=400)
        full_path = (root / rel_path).resolve()
        if not str(full_path).startswith(str(root.resolve())):
            return JSONResponse({"error": "path traversal denied"}, status_code=403)
        if not full_path.is_file():
            return JSONResponse({"error": "file not found"}, status_code=404)
        try:
            if sys.platform == "win32":
                os.startfile(str(full_path))
            else:
                subprocess.Popen(["open", str(full_path)])
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)
        return {"ok": True}

    @app.post("/api/sessions/{session_id}/open-explorer")
    async def post_open_explorer(session_id: str, body: dict):
        session = _get_session(registry, session_id)
        root = session.working_root
        rel_path = str(body.get("path") or "").strip()
        if not rel_path:
            return JSONResponse({"error": "path is required"}, status_code=400)
        href_str = rel_path.replace("\\", "/")
        if href_str.rstrip("/") == f".council/sessions/{session_id}":
            full_path = session.session_dir.resolve()
        else:
            full_path = (root / rel_path).resolve()
            if not str(full_path).startswith(str(root.resolve())):
                return JSONResponse({"error": "path traversal denied"}, status_code=403)
        try:
            if sys.platform == "win32":
                subprocess.Popen(["explorer", str(full_path)])
            else:
                subprocess.Popen(["open", str(full_path)])
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)
        return {"ok": True}

    @app.post("/api/sessions/{session_id}/attachments")
    async def post_attachment(session_id: str, body: dict):
        session = _get_session(registry, session_id)
        try:
            raw, media_type = _decode_image_upload(body)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        attachments_dir = session.session_dir / "attachments"
        attachments_dir.mkdir(parents=True, exist_ok=True)
        name = _new_attachment_name(media_type)
        path = attachments_dir / name
        path.write_bytes(raw)
        url = f"/api/sessions/{session.id}/attachments/{name}"
        root = session.working_root
        rel_path = path.relative_to(root) if root in path.parents else path
        markdown = f"![pasted image]({url})\n\nAttachment: `{rel_path}`"
        await session.add_trace("system", "image pasted", str(rel_path))
        return {
            "ok": True,
            "name": name,
            "url": url,
            "path": str(path),
            "markdown": markdown,
        }

    @app.get("/api/sessions/{session_id}/attachments/{name}")
    async def get_attachment(session_id: str, name: str):
        session = _get_session(registry, session_id)
        if "/" in name or "\\" in name or name in ("", ".", ".."):
            raise HTTPException(status_code=404)
        path = (session.session_dir / "attachments" / name).resolve()
        attachments_dir = (session.session_dir / "attachments").resolve()
        if not str(path).startswith(str(attachments_dir)) or not path.is_file():
            raise HTTPException(status_code=404)
        media_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        if media_type not in IMAGE_UPLOAD_TYPES:
            raise HTTPException(status_code=404)
        return FileResponse(path, media_type=media_type)

    @app.patch("/api/sessions/{session_id}/config")
    async def patch_session_config(session_id: str, body: dict):
        session = _get_session(registry, session_id)
        changes = _allowed_config_changes(body)
        if not changes:
            return JSONResponse({"error": "no supported config changes"}, status_code=400)
        session_changes, global_changes = _split_sensitive_config_changes(changes)
        if global_changes:
            await registry.update_global_config(global_changes)
        if session_changes:
            await session.update_config(session_changes)
        return {"ok": True, "agents": build_agent_info(registry.config, session.state)}

    @app.patch("/api/config")
    async def patch_config(body: dict):
        changes = _allowed_config_changes(body)
        if not changes:
            return JSONResponse({"error": "no supported config changes"}, status_code=400)
        await registry.update_global_config(changes)
        return {"ok": True, "agents": build_agent_info(registry.config)}

    @app.post("/api/sessions/{session_id}/agents")
    async def add_agent(session_id: str, body: dict):
        session = _get_session(registry, session_id)
        agent_name = str(body.get("name") or "").strip().lower()
        if not agent_name or not re.fullmatch(r"[a-z][a-z0-9_-]*", agent_name):
            raise HTTPException(status_code=400, detail="Invalid agent name. Use lowercase letters, digits, hyphens, or underscores (must start with a letter).")
        current_agents = session._session_agents()
        if agent_name in current_agents:
            raise HTTPException(status_code=409, detail=f"Agent '{agent_name}' already exists.")
        agents_list = list(session.state.get("agents") or current_agents)
        agents_list.append(agent_name)
        session.state["agents"] = agents_list
        provider = str(body.get("provider") or "hermes_api")
        if provider:
            providers = session.state.setdefault("providers", {})
            providers[agent_name] = provider
        model = str(body.get("model") or "")
        if model:
            models = session.state.setdefault("models", {})
            models[agent_name] = model
        session.save_state()
        logger.info(
            "Agent slot added",
            f"session={session.name} ({session.id}) agent={agent_name} provider={provider}",
        )
        await session.broadcast_status()
        return {"ok": True, "agents": build_agent_info(registry.config, session.state)}

    @app.delete("/api/sessions/{session_id}/agents/{agent}")
    async def remove_agent(session_id: str, agent: str):
        session = _get_session(registry, session_id)
        agent = agent.strip().lower()
        if agent in AGENTS:
            raise HTTPException(status_code=400, detail=f"Cannot remove built-in agent '{agent}'.")
        current_agents = session._session_agents()
        if agent not in current_agents:
            raise HTTPException(status_code=404, detail=f"Agent '{agent}' not found.")
        agents_list = [a for a in current_agents if a != agent]
        session.state["agents"] = agents_list if agents_list != list(AGENTS) else []
        session.save_state()
        logger.info(
            "Agent slot removed",
            f"session={session.name} ({session.id}) agent={agent}",
        )
        await session.broadcast_status()
        return {"ok": True, "agents": build_agent_info(registry.config, session.state)}

    @app.get("/api/sessions/{session_id}/completions")
    async def get_completions(session_id: str, q: str = "", kind: str = "all", limit: int = 30):
        session = _get_session(registry, session_id)
        q_low = q.lower()
        agents = []
        files = []
        if kind in ("all", "agents"):
            aliases = session.effective_config().get("aliases", {})
            session_agents = session._session_agents()
            agents = [a for a in list_agents(aliases, agents=session_agents) if a.lower().startswith(q_low)]
        if kind in ("all", "files"):
            all_files = list_project_files(session.working_root)
            if q_low:
                files = [f for f in all_files if q_low in f.lower()][:limit]
            else:
                files = all_files[:limit]
        return {"agents": agents, "files": files}

    @app.get("/api/sessions/{session_id}/status")
    async def get_status(session_id: str):
        session = _get_session(registry, session_id)
        return {
            "busy": session.busy,
            "compacting": session.compacting,
            "current_agent": session.current_agent,
            "agent": session.current_agent,
            "current_dispatch": session.current_dispatch,
            "active_dispatches": session.active_dispatches_snapshot(),
            "dispatch_queue": session.dispatch_queue_snapshot(),
            "project": session.project_name,
            "session": session.metadata(),
            "agents": build_agent_info(registry.config, session.state),
            "global_agents": build_agent_info(registry.config),
            "dispatch": session.effective_config().get("dispatch", {}),
            "global_dispatch": registry.config.get("dispatch", {}),
            "compact": session.compact_status(),
            "token_totals": session.token_totals(),
        }

    @app.post("/api/sessions/{session_id}/reset_token_stats")
    async def post_reset_token_stats(session_id: str, agent: str = ""):
        session = _get_session(registry, session_id)
        if session.busy or session.active_dispatches or session.dispatch_queue:
            return JSONResponse(
                {"error": "cannot reset token stats while agents are running or queued"},
                status_code=409,
            )
        agent = str(agent or "").strip().lower()
        if agent and agent not in AGENTS:
            return JSONResponse(
                {"error": f"unknown agent: {agent}"},
                status_code=400,
            )
        result = session.reset_token_stats(agent=agent or None)
        label = f"@{agent}" if agent else "all"
        registry.add_global_log(
            f"Token stats reset ({label})",
            f"session={session.name} ({session.id}) agent={agent or 'all'}",
        )
        await session.broadcast_status()
        return {"ok": True, **result}

    @app.get("/api/sessions/{session_id}/trace")
    async def get_trace(session_id: str):
        session = _get_session(registry, session_id)
        return {"events": session.trace_events}

    @app.post("/api/sessions/{session_id}/prompt_preview")
    async def post_prompt_preview(session_id: str, body: dict):
        session = _get_session(registry, session_id)
        draft_text = str(body.get("draft_text") or "").strip()
        agents = body.get("agents")
        if not isinstance(agents, list) or not agents:
            return JSONResponse(
                {"error": "agents list is required"},
                status_code=400,
            )
        config = session.effective_config()
        session_agents = _active_agents(session.config, session.state)
        dispatch = config.get("dispatch", {})
        aliases = config.get("aliases", {})
        context_windows = config.get("context_windows", {})
        results: dict[str, dict] = {}
        for agent in agents:
            if agent not in session_agents:
                continue
            provider = config.get("providers", {}).get(agent, "")
            family = runtime_family_for_provider(provider, agent)
            max_chars_map = {
                "claude": int(dispatch.get("max_prompt_chars", 25000)),
                "codex": int(dispatch.get("max_prompt_chars", 25000)),
                "deepseek": int(dispatch.get("max_prompt_chars", 25000)),
                "hermes": int(dispatch.get(
                    "council_injection_max_chars",
                    dispatch.get("hermes_max_prompt_chars", 6000),
                )),
            }
            max_chars = max_chars_map.get(family, int(dispatch.get("max_prompt_chars", 25000)))
            min_tail = 8
            inc_memory = True
            inc_actions = True
            rules_budget = None
            summary_budget = None
            if family == "hermes":
                min_tail = int(dispatch.get("hermes_min_chat_tail_turns", 2))
                inc_memory = str(dispatch.get("hermes_include_agent_memory", "false")).strip().lower() in {"1", "true", "yes", "on"}
                inc_actions = str(dispatch.get("hermes_include_recent_actions", "false")).strip().lower() in {"1", "true", "yes", "on"}
                rules_budget = int(dispatch.get("hermes_project_rules_max_chars", 1500))
                summary_budget = int(dispatch.get("hermes_compaction_summary_max_chars", 1500))
            attachments = dispatch.get("attachments", {})
            attachment_policy = str(attachments.get(agent, "") if isinstance(attachments, dict) else "")
            role = config.get("roles", {}).get(agent, "")
            breakdown = preview_prompt(
                agent,
                session.project_root,
                session.chat_path,
                draft_text=draft_text,
                max_chars=max_chars,
                role=role,
                session_dir=session.session_dir,
                session_name=session.name,
                aliases=aliases,
                compactions_path=session.compactions_path,
                attachment_policy=attachment_policy,
                min_chat_tail_turns=min_tail,
                include_agent_memory=inc_memory,
                include_recent_actions=inc_actions,
                project_rules_max_chars=rules_budget,
                compaction_summary_max_chars=summary_budget,
                council_context_hint=family == "hermes",
            )
            context_window = int(context_windows.get(agent, 0)) or 200000
            breakdown["context_window"] = context_window
            results[agent] = breakdown
        return results

    @app.get("/api/sessions/{session_id}/events")
    async def get_events(session_id: str):
        session = _get_session(registry, session_id)
        events_path = session.events_path
        if not events_path.exists():
            return {"turns": []}
        turns: list[dict] = []
        memory_cache: dict[str, dict | None] = {}
        for line_idx, line in enumerate(events_path.read_text(encoding="utf-8", errors="replace").splitlines()):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("kind") == "compaction":
                turns.clear()
                turns.append({
                    "event_line_idx": line_idx,
                    "author": "system",
                    "display_ts": _compaction_display_ts(event),
                    "prompt_tokens_est": None,
                    "response_tokens_est": None,
                    "token_usage": None,
                    "metadata": {"compaction_id": event.get("compaction_id")},
                    "tool_calls": [],
                    "agent_memory": None,
                })
                continue
            if event.get("kind") not in ("user_turn", "agent_turn", "system_turn"):
                continue
            meta = event.get("metadata") or {}
            author = event.get("author") or "system"
            if author not in memory_cache:
                memory_cache[author] = _turn_memory_snapshot(session, author)
            memory = memory_cache[author]
            turns.append({
                "event_line_idx": line_idx,
                "author": author,
                "display_ts": event.get("display_ts") or "",
                "prompt_tokens_est": event.get("prompt_tokens_est") or meta.get("prompt_tokens_est"),
                "response_tokens_est": event.get("response_tokens_est") or meta.get("response_tokens_est"),
                "token_usage": event.get("token_usage"),
                "metadata": meta,
                "tool_calls": meta.get("tool_calls") or [],
                "agent_memory": memory,
            })
        return {"turns": turns}

    @app.websocket("/ws/{session_id}")
    async def websocket_endpoint(ws: WebSocket, session_id: str):
        try:
            session = registry.get(session_id)
        except KeyError:
            await ws.accept()
            await ws.send_json({"type": "error", "msg": "session not found"})
            await ws.close(code=1008)
            return

        await ws.accept()
        queue = session.subscribe()
        try:
            while ws.client_state == WebSocketState.CONNECTED:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30)
                    if ws.client_state == WebSocketState.CONNECTED:
                        await ws.send_json(event)
                except asyncio.TimeoutError:
                    if ws.client_state == WebSocketState.CONNECTED:
                        await ws.send_json({"type": "ping"})
                except WebSocketDisconnect:
                    break
        finally:
            session.unsubscribe(queue)

    return app
