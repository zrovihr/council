"""FastAPI app: multi-session routes, websocket, and static file serving."""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from starlette.websockets import WebSocketState

from .state import Session, SessionRegistry, build_agent_info
from .daemon import session_daemon_loop
from .summarizer import compact_chat
from .completions import list_agents, list_project_files

logger = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


def _get_session(registry: SessionRegistry, session_id: str) -> Session:
    try:
        return registry.get(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="session not found")


def _allowed_config_changes(body: dict) -> dict:
    allowed_sections = {"models", "effort", "roles"}
    return {
        section: value
        for section, value in body.items()
        if section in allowed_sections and isinstance(value, dict)
    }


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
        session.append_turn("you", text)
        await session.notify_chat_update()
        return {"ok": True}

    @app.post("/api/sessions/{session_id}/compact")
    async def post_compact(session_id: str):
        session = _get_session(registry, session_id)
        await compact_chat(session, session.effective_config())
        await session.notify_chat_update()
        return {"ok": True}

    @app.post("/api/sessions/{session_id}/cancel")
    async def post_cancel(session_id: str):
        session = _get_session(registry, session_id)
        cancelled = await session.cancel_current_dispatch()
        if not cancelled:
            return JSONResponse({"error": "no active dispatch"}, status_code=409)
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
            agents = [a for a in list_agents() if a.lower().startswith(q_low)]
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
            "project": session.project_name,
            "session": session.metadata(),
            "agents": build_agent_info(registry.config, session.state),
            "global_agents": build_agent_info(registry.config),
        }

    @app.get("/api/sessions/{session_id}/trace")
    async def get_trace(session_id: str):
        session = _get_session(registry, session_id)
        return {"events": session.trace_events}

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
