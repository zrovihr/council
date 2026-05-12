"""FastAPI app: routes, websocket, and static file serving."""

import json
import asyncio
import logging
from pathlib import Path
from datetime import datetime
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from starlette.websockets import WebSocketState

from .state import AppState, build_agent_info
from .daemon import daemon_loop
from .summarizer import compact_chat
from .completions import list_agents, list_project_files

logger = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


def _append_user_turn(chat_path: Path, text: str):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    turn = f"\n## [@you] {timestamp}\n{text}\n\n---\n"
    with open(chat_path, "a", encoding="utf-8") as f:
        f.write(turn)


@asynccontextmanager
async def lifespan(app: FastAPI):
    state = app.state.council_state
    daemon_task = asyncio.create_task(daemon_loop(state))
    app.state.council_daemon = daemon_task
    yield
    daemon_task.cancel()
    try:
        await daemon_task
    except asyncio.CancelledError:
        pass


def create_app(state: AppState) -> FastAPI:
    app = FastAPI(lifespan=lifespan)
    app.state.council_state = state

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

    @app.get("/api/chat")
    async def get_chat():
        chat_path = state.chat_path
        if chat_path.exists():
            return {"text": chat_path.read_text(encoding="utf-8", errors="replace")}
        return {"text": ""}

    @app.post("/api/send")
    async def post_send(body: dict):
        text = body.get("text", "")
        if not text.strip():
            return JSONResponse({"error": "empty message"}, status_code=400)

        _append_user_turn(state.chat_path, text)
        await state.notify_chat_update()
        return {"ok": True}

    @app.post("/api/compact")
    async def post_compact():
        await compact_chat(state.chat_path, state.archive_dir, state.config)
        await state.notify_chat_update()
        return {"ok": True}

    @app.post("/api/cancel")
    async def post_cancel():
        cancelled = await state.cancel_current_dispatch()
        if not cancelled:
            return JSONResponse({"error": "no active dispatch"}, status_code=409)
        return {"ok": True}

    @app.patch("/api/config")
    async def patch_config(body: dict):
        allowed_sections = {"models", "effort", "roles"}
        changes = {
            section: value
            for section, value in body.items()
            if section in allowed_sections and isinstance(value, dict)
        }
        if not changes:
            return JSONResponse({"error": "no supported config changes"}, status_code=400)
        await state.update_config(changes)
        return {"ok": True, "agents": build_agent_info(state.config)}

    @app.get("/api/completions")
    async def get_completions(q: str = "", kind: str = "all", limit: int = 30):
        q_low = q.lower()
        agents = []
        files = []
        if kind in ("all", "agents"):
            agents = [a for a in list_agents() if a.lower().startswith(q_low)]
        if kind in ("all", "files"):
            all_files = list_project_files(state.project_root)
            if q_low:
                files = [f for f in all_files if q_low in f.lower()][:limit]
            else:
                files = all_files[:limit]
        return {"agents": agents, "files": files}

    @app.get("/api/status")
    async def get_status():
        return {
            "busy": state.busy,
            "current_agent": state.current_agent,
            "agent": state.current_agent,
            "project": state.project_name,
            "agents": build_agent_info(state.config),
        }

    @app.get("/api/trace")
    async def get_trace():
        return {"events": state.trace_events}

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        await ws.accept()
        queue = state.subscribe()
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
            state.unsubscribe(queue)

    return app
