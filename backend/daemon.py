"""Daemon: watches each session chat.md for @mentions and dispatches agents."""

import asyncio
import json
import re
import logging
from datetime import datetime
from pathlib import Path

from .state import Session, SessionRegistry
from .prompt_builder import build_prompt
from .agent_memory import write_agent_memory
from .dispatcher import (dispatch_claude, dispatch_codex, dispatch_deepseek,
                            DispatchResult, estimate_tokens)
from .summarizer import compact_chat
from .mentions import find_agent_mentions, find_tail_mention, neutralize_agent_mentions

logger = logging.getLogger(__name__)

TURN_HEADER_RE = re.compile(r"^##\s+\[@(\w+)\]\s+(.+)$")


def _get_binary(config: dict, agent: str) -> str:
    return (config.get("binaries", {}).get(agent)
            or config.get("models", {}).get(agent)
            or agent)


def _get_model(config: dict, agent: str) -> str:
    return config.get("models", {}).get(agent, "")


def _get_effort(config: dict, agent: str) -> str:
    return config.get("effort", {}).get(agent, "")


def _get_role(config: dict, agent: str) -> str:
    return config.get("roles", {}).get(agent, "")


def _get_model_deepseek(config: dict) -> str:
    return config.get("models", {}).get(
        "deepseek_pro", "deepseek/deepseek-v4-pro"
    )


def _parse_turns(text: str) -> list[dict]:
    turns = []
    current = None

    def finish_current() -> None:
        nonlocal current
        if current is None:
            return
        current["body"] = re.sub(r"\n?---\s*$", "", current["body"])
        turns.append(current)
        current = None

    for line in text.splitlines():
        m = TURN_HEADER_RE.match(line)
        if m:
            finish_current()
            current = {"author": m.group(1), "header": line.strip(), "body": ""}
        elif current is not None:
            if current["body"]:
                current["body"] += "\n"
            current["body"] += line
    finish_current()
    return turns


def _read_latest_turn_header(chat_path: Path) -> str:
    if not chat_path.exists():
        return ""

    text = chat_path.read_text(encoding="utf-8", errors="replace")
    turns = _parse_turns(text)
    return turns[-1]["header"] if turns else ""


def _find_header_index(turns: list[dict], header: str) -> int | None:
    for i, turn in enumerate(turns):
        if turn["header"] == header:
            return i
    return None


def _mention_key(header: str, agent: str) -> str:
    return f"{header}\t{agent}"


def _load_dispatch_ledger(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("Failed to read dispatch ledger %s", path)
        return set()
    entries = data.get("dispatched") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        return set()
    return {str(entry) for entry in entries}


def _save_dispatch_ledger(path: Path, dispatched: set[str]) -> None:
    path.write_text(
        json.dumps({"dispatched": sorted(dispatched)}, indent=2) + "\n",
        encoding="utf-8",
    )


def _turn_mentions(turn: dict) -> list[str]:
    author = turn["author"]
    if author == "you":
        return find_agent_mentions(turn["body"])
    if author in ("claude", "codex", "deepseek"):
        mention = find_tail_mention(turn["body"])
        return [mention] if mention else []
    return []


def _request_still_valid(chat_path: Path, request: dict[str, str]) -> bool:
    if not chat_path.exists():
        return False

    text = chat_path.read_text(encoding="utf-8", errors="replace")
    turns = _parse_turns(text)
    if not turns:
        return False

    source = request["source"]
    for turn in turns:
        if turn["header"] != request["header"]:
            continue
        author = turn["author"]
        if source == "user" and author != "you":
            continue
        if source == "agent" and author not in ("claude", "codex", "deepseek"):
            continue
        return request["agent"] in _turn_mentions(turn)
    return False


def _agent_timeout(config: dict, agent: str) -> int:
    dispatch = config.get("dispatch", {})
    default_timeout = dispatch.get("timeout_seconds", 300)
    return dispatch.get(f"{agent}_timeout_seconds", default_timeout)


def _format_timeout(timeout: int) -> str:
    return "none" if timeout <= 0 else f"{timeout}s"


def _format_usage(usage: dict[str, int]) -> str:
    if not usage:
        return "tokens=unavailable"
    parts = []
    if "prompt_tokens" in usage:
        parts.append(f"prompt_tokens={usage['prompt_tokens']}")
    if "completion_tokens" in usage:
        parts.append(f"completion_tokens={usage['completion_tokens']}")
    if "total_tokens" in usage:
        parts.append(f"total_tokens={usage['total_tokens']}")
    return " ".join(parts) if parts else "tokens=unavailable"


async def session_daemon_loop(session: Session, registry: SessionRegistry):
    chat_path = session.chat_path

    last_processed_header = _read_latest_turn_header(chat_path)
    dispatch_ledger_path = session.session_dir / "dispatch-ledger.json"
    dispatched_mentions = _load_dispatch_ledger(dispatch_ledger_path)
    if chat_path.exists() and not dispatched_mentions:
        text = chat_path.read_text(encoding="utf-8", errors="replace")
        for turn in _parse_turns(text):
            for agent in _turn_mentions(turn):
                dispatched_mentions.add(_mention_key(turn["header"], agent))
        _save_dispatch_ledger(dispatch_ledger_path, dispatched_mentions)
    pending_mentions: list[dict[str, str]] = []
    pending_changed = asyncio.Condition()

    async def enqueue_mention(agent: str, source: str, header: str) -> None:
        nonlocal pending_mentions

        key = _mention_key(header, agent)
        if key in dispatched_mentions:
            return
        dispatched_mentions.add(key)
        _save_dispatch_ledger(dispatch_ledger_path, dispatched_mentions)

        async with pending_changed:
            if source == "user":
                pending_mentions.append({
                    "agent": agent,
                    "source": source,
                    "header": header,
                })
                pending_changed.notify()
            else:
                if session.current_agent == agent:
                    return
                pending_mentions.append({
                    "agent": agent,
                    "source": source,
                    "header": header,
                })
                pending_changed.notify()

        if source == "user" and session.current_agent == agent:
            await session.cancel_current_dispatch()

    async def next_mention() -> dict[str, str]:
        async with pending_changed:
            while not pending_mentions:
                await pending_changed.wait()
            return pending_mentions.pop(0)

    async def watcher():
        nonlocal last_processed_header
        while True:
            try:
                if chat_path.exists():
                    text = chat_path.read_text(encoding="utf-8", errors="replace")
                    turns = _parse_turns(text)

                    start_idx = 0
                    if last_processed_header:
                        header_idx = _find_header_index(turns, last_processed_header)
                        if header_idx is None:
                            start_idx = 0
                            last_processed_header = ""
                        else:
                            start_idx = header_idx + 1

                    for turn in turns[start_idx:]:
                        author = turn["author"]
                        source = None
                        if author == "you":
                            source = "user"
                        elif author in ("claude", "codex", "deepseek"):
                            source = "agent"
                        if source:
                            for mention in _turn_mentions(turn):
                                await enqueue_mention(mention, source, turn["header"])
                        last_processed_header = turn["header"]

            except Exception:
                logger.exception("Watcher error")

            await asyncio.sleep(1)

    async def worker():
        nonlocal last_processed_header
        while True:
            request = await next_mention()
            if not _request_still_valid(chat_path, request):
                continue
            mention = request["agent"]
            config = session.effective_config()
            max_chars = config["dispatch"]["max_prompt_chars"]
            auto_threshold = config["compact"]["auto_threshold_lines"]
            await session.set_busy(mention)
            captured_output_parts: list[str] = []
            try:
                role = _get_role(config, mention)
                prompt = build_prompt(
                    mention,
                    session.project_root,
                    chat_path,
                    max_chars,
                    role=role,
                    session_dir=session.session_dir,
                )
                binary = _get_binary(config, mention)
                model = _get_model(config, mention)
                effort = _get_effort(config, mention)
                if mention == "deepseek":
                    runtime = _get_model_deepseek(config)
                    command_hint = (
                        f"opencode run -m {runtime} "
                        "--dangerously-skip-permissions <instruction> "
                        "--file=<prompt.md>"
                    )
                elif mention == "claude":
                    runtime = f"{binary} CLI (model={model or 'CLI default'})"
                    command_hint = (
                        f"{binary} --dangerously-skip-permissions "
                        f"--add-dir {registry.council_root} -p"
                    )
                else:
                    runtime = f"{binary} exec (model={model or 'CLI default'})"
                    command_hint = (
                        f"{binary} exec --dangerously-bypass-approvals-and-sandbox "
                        "--output-last-message <file> -"
                    )
                await session.add_trace(
                    mention,
                    "dispatch started",
                    f"runtime={runtime} command={command_hint} "
                    f"timeout={_format_timeout(_agent_timeout(config, mention))} "
                    f"prompt_chars={len(prompt)} "
                    f"prompt_tokens_est={estimate_tokens(prompt)} "
                    f"role_chars={len(role)} "
                    f"cwd={session.project_root}",
                )

                async def trace_agent_output(source: str, text: str):
                    captured_output_parts.append(f"[{source}]\n{text}")
                    await session.add_trace(
                        mention,
                        f"{source}",
                        text[:2000],
                    )

                async def run_dispatch() -> DispatchResult:
                    if mention == "claude":
                        return await dispatch_claude(
                            prompt, session.project_root,
                            _agent_timeout(config, mention),
                            binary=binary, model=model, effort=effort,
                            on_output=trace_agent_output,
                        )
                    if mention == "codex":
                        return await dispatch_codex(
                            prompt, session.project_root,
                            _agent_timeout(config, mention),
                            binary=binary, model=model, effort=effort,
                            on_output=trace_agent_output,
                        )
                    if mention == "deepseek":
                        async def trace_opencode_output(source: str, text: str):
                            captured_output_parts.append(f"[opencode {source}]\n{text}")
                            await session.add_trace(
                                "deepseek",
                                f"opencode {source}",
                                text[:2000],
                            )

                        return await dispatch_deepseek(
                            prompt, session.project_root,
                            _agent_timeout(config, mention),
                            model=_get_model_deepseek(config),
                            effort=effort,
                            on_output=trace_opencode_output,
                        )
                    raise ValueError(f"Unknown agent: {mention}")

                dispatch_task = asyncio.create_task(run_dispatch())
                await session.set_dispatch_task(dispatch_task)
                try:
                    result = await dispatch_task
                finally:
                    await session.set_dispatch_task(None)

                response = result.text
                artifact_path = write_agent_memory(
                    session.session_dir,
                    mention,
                    "completed",
                    final_response=response,
                    captured_output="\n\n".join(captured_output_parts),
                    usage=result.usage,
                )
                await session.add_trace(
                    mention,
                    "dispatch completed",
                    f"response_chars={len(response)} "
                    f"response_tokens_est={estimate_tokens(response)} "
                    f"{_format_usage(result.usage)} "
                    f"memory={artifact_path.relative_to(session.session_dir) if artifact_path else 'none'}",
                )
                session.append_turn(
                    mention, response,
                    usage=result.usage,
                    metadata={
                        "prompt_tokens_est": estimate_tokens(prompt),
                        "response_tokens_est": estimate_tokens(response),
                    },
                )
                await session.notify_chat_update()

                if chat_path.exists():
                    text = chat_path.read_text(encoding="utf-8", errors="replace")
                    line_count = text.count("\n") + 1
                    if line_count > auto_threshold:
                        await compact_chat(session, config)
                        await session.notify_chat_update()
                        last_processed_header = ""

            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():
                    raise
                artifact_path = write_agent_memory(
                    session.session_dir,
                    mention,
                    "cancelled",
                    captured_output="\n\n".join(captured_output_parts),
                    error="Stopped by user.",
                )
                await session.add_trace(mention, "dispatch cancelled", "Stopped by user.")
                safe_body = neutralize_agent_mentions(
                    f"agent {mention} dispatch cancelled by user. Partial effort saved for @{mention}: "
                    f"`{artifact_path.relative_to(session.session_dir) if artifact_path else 'none'}`"
                )
                session.append_turn("system", safe_body, kind="system_turn")
                await session.notify_chat_update()
            except Exception as e:
                logger.exception("Dispatch error")
                artifact_path = write_agent_memory(
                    session.session_dir,
                    mention,
                    "failed",
                    captured_output="\n\n".join(captured_output_parts),
                    error=str(e),
                )
                await session.add_trace(mention, "dispatch failed", str(e))
                safe_reason = neutralize_agent_mentions(str(e)).strip()
                session.append_turn(
                    "system",
                    f"error: agent {mention} dispatch failed: {safe_reason}\n\n"
                    f"Partial effort saved for @{mention}: "
                    f"`{artifact_path.relative_to(session.session_dir) if artifact_path else 'none'}`",
                    kind="system_turn",
                )
                await session.notify_chat_update()
            finally:
                await session.set_idle()

    await asyncio.gather(watcher(), worker())
