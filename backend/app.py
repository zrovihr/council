"""FastAPI app: multi-session routes, websocket, and static file serving."""

import asyncio
import json
import logging
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from starlette.websockets import WebSocketState

from .state import Session, SessionRegistry, build_agent_info, rebuild_chat_from_events
from .daemon import session_daemon_loop
from .summarizer import compact_chat
from .completions import list_agents, list_project_files

logger = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
TURN_KINDS = {"user_turn", "agent_turn", "system_turn"}


def _get_session(registry: SessionRegistry, session_id: str) -> Session:
    try:
        return registry.get(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="session not found")


def _allowed_config_changes(body: dict) -> dict:
    allowed_sections = {
        "models",
        "effort",
        "roles",
        "dispatch",
        "providers",
        "api_keys",
        "aliases",
    }
    return {
        section: value
        for section, value in body.items()
        if section in allowed_sections and isinstance(value, dict)
    }


def _load_event_lines(session: Session) -> tuple[list[str], list[tuple[int, dict]]]:
    lines = session.events_path.read_text(encoding="utf-8").splitlines()
    turns: list[tuple[int, dict]] = []
    for line_idx, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("kind") in TURN_KINDS:
            turns.append((line_idx, event))
    return lines, turns


def _find_turn_line(body: dict, turns: list[tuple[int, dict]]) -> int | None:
    author = body.get("author")
    display_ts = body.get("display_ts")
    original_text = body.get("original_text")

    if isinstance(author, str) and isinstance(display_ts, str) and isinstance(original_text, str):
        for line_idx, event in turns:
            if (
                event.get("author") == author
                and event.get("display_ts") == display_ts
                and (event.get("text") or "") == original_text.rstrip()
            ):
                return line_idx

    turn_index = body.get("index")
    if isinstance(turn_index, int) and 0 <= turn_index < len(turns):
        return turns[turn_index][0]
    return None


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

    @app.post("/api/sessions")
    async def post_session(body: dict):
        name = str(body.get("name") or "").strip()
        project_root = str(body.get("project_root") or "").strip()
        if not project_root:
            return JSONResponse({"error": "project_root is required"}, status_code=400)
        session = registry.create_session(name or "untitled", project_root, activate=True)
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
        if session.chat_path.exists():
            return {"text": session.chat_path.read_text(encoding="utf-8", errors="replace")}
        return {"text": ""}

    @app.post("/api/sessions/{session_id}/send")
    async def post_send(session_id: str, body: dict):
        session = _get_session(registry, session_id)
        text = str(body.get("text", ""))
        if not text.strip():
            return JSONResponse({"error": "empty message"}, status_code=400)
        dispatch_mode = str(body.get("dispatch_mode") or "parallel").lower()
        if dispatch_mode not in ("parallel", "queued"):
            return JSONResponse({"error": "invalid dispatch_mode"}, status_code=400)
        session.append_turn("you", text, metadata={"dispatch_mode": dispatch_mode})
        await session.notify_chat_update()
        return {"ok": True}

    @app.post("/api/sessions/{session_id}/compact")
    async def post_compact(session_id: str):
        session = _get_session(registry, session_id)
        await session.set_busy("summarizer")
        try:
            await compact_chat(session, session.effective_config())
        finally:
            await session.set_idle()
        await session.notify_chat_update()
        return {"ok": True}

    @app.post("/api/sessions/{session_id}/erase")
    async def post_erase(session_id: str):
        session = _get_session(registry, session_id)
        session.chat_path.write_text("", encoding="utf-8")
        session.events_path.write_text("", encoding="utf-8")
        session.trace_events.clear()
        await session.add_trace("system", "chat erased")
        await session.notify_chat_update()
        return {"ok": True}

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
        del lines[target_idx]
        session.events_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        rebuild_chat_from_events(
            session.events_path,
            session.chat_path,
            session.compactions_path,
        )
        await session.add_trace("system", f"turn {turn_index} erased")
        await session.notify_chat_update()
        return {"ok": True}

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
        event["text"] = new_text
        lines[target_idx] = json.dumps(event, ensure_ascii=True)
        session.events_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        rebuild_chat_from_events(
            session.events_path,
            session.chat_path,
            session.compactions_path,
        )
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

        async def _delayed_exit():
            await asyncio.sleep(0.5)
            os._exit(42)

        asyncio.create_task(_delayed_exit())
        return {"ok": True}

    @app.post("/api/sessions/{session_id}/open-file")
    async def post_open_file(session_id: str, body: dict):
        session = _get_session(registry, session_id)
        rel_path = str(body.get("path") or "").strip()
        if not rel_path:
            return JSONResponse({"error": "path is required"}, status_code=400)
        full_path = (session.project_root / rel_path).resolve()
        if not str(full_path).startswith(str(session.project_root.resolve())):
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
        rel_path = str(body.get("path") or "").strip()
        if not rel_path:
            return JSONResponse({"error": "path is required"}, status_code=400)
        full_path = (session.project_root / rel_path).resolve()
        if not str(full_path).startswith(str(session.project_root.resolve())):
            return JSONResponse({"error": "path traversal denied"}, status_code=403)
        try:
            if sys.platform == "win32":
                subprocess.Popen(["explorer", f"/select,{str(full_path)}"])
            else:
                subprocess.Popen(["open", "-R", str(full_path)])
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)
        return {"ok": True}

    @app.patch("/api/sessions/{session_id}/config")
    async def patch_session_config(session_id: str, body: dict):
        session = _get_session(registry, session_id)
        changes = _allowed_config_changes(body)
        if not changes:
            return JSONResponse({"error": "no supported config changes"}, status_code=400)
        await session.update_config(changes)
        return {"ok": True, "agents": build_agent_info(registry.config, session.state)}

    @app.patch("/api/config")
    async def patch_config(body: dict):
        changes = _allowed_config_changes(body)
        if not changes:
            return JSONResponse({"error": "no supported config changes"}, status_code=400)
        await registry.update_global_config(changes)
        return {"ok": True, "agents": build_agent_info(registry.config)}

    @app.get("/api/sessions/{session_id}/completions")
    async def get_completions(session_id: str, q: str = "", kind: str = "all", limit: int = 30):
        session = _get_session(registry, session_id)
        q_low = q.lower()
        agents = []
        files = []
        if kind in ("all", "agents"):
            aliases = session.effective_config().get("aliases", {})
            agents = [a for a in list_agents(aliases) if a.lower().startswith(q_low)]
        if kind in ("all", "files"):
            all_files = list_project_files(session.project_root)
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
        }

    @app.get("/api/sessions/{session_id}/trace")
    async def get_trace(session_id: str):
        session = _get_session(registry, session_id)
        return {"events": session.trace_events}

    @app.get("/api/sessions/{session_id}/events")
    async def get_events(session_id: str):
        session = _get_session(registry, session_id)
        events_path = session.events_path
        if not events_path.exists():
            return {"turns": []}
        turns: list[dict] = []
        for line in events_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("kind") not in ("user_turn", "agent_turn", "system_turn"):
                continue
            meta = event.get("metadata") or {}
            turns.append({
                "author": event.get("author") or "system",
                "display_ts": event.get("display_ts") or "",
                "prompt_tokens_est": event.get("prompt_tokens_est") or meta.get("prompt_tokens_est"),
                "response_tokens_est": event.get("response_tokens_est") or meta.get("response_tokens_est"),
                "token_usage": event.get("token_usage"),
                "metadata": meta,
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
