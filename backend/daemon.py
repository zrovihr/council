"""Daemon: watches chat.md for @mentions and dispatches agents."""

import asyncio
import re
import logging
from datetime import datetime
from pathlib import Path

from .state import AppState
from .prompt_builder import build_prompt
from .dispatcher import (dispatch_claude, dispatch_codex, dispatch_deepseek,
                            DispatchResult)
from .summarizer import compact_chat
from .mentions import find_first_mention, find_tail_mention, neutralize_agent_mentions

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
    for line in text.splitlines():
        m = TURN_HEADER_RE.match(line)
        if m:
            if current is not None:
                turns.append(current)
            current = {"author": m.group(1), "header": line.strip(), "body": ""}
        elif current is not None:
            if line.strip() == "---":
                turns.append(current)
                current = None
            else:
                if current["body"]:
                    current["body"] += "\n"
                current["body"] += line
    if current is not None:
        turns.append(current)
    return turns


def _read_latest_turn_header(chat_path: Path) -> str:
    if not chat_path.exists():
        return ""

    text = chat_path.read_text(encoding="utf-8", errors="replace")
    turns = _parse_turns(text)
    return turns[-1]["header"] if turns else ""


def _append_turn(chat_path: Path, author: str, body: str, metadata: dict | None = None):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    turn = f"\n## [@{author}] {timestamp}\n{body}\n\n---\n"
    with open(chat_path, "a", encoding="utf-8") as f:
        f.write(turn)


def _append_error(chat_path: Path, agent: str, reason: str):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    safe_reason = neutralize_agent_mentions(reason).strip()
    turn = (
        f"\n## [@system] {timestamp}\n"
        f"error: agent {agent} dispatch failed: {safe_reason}\n\n---\n"
    )
    with open(chat_path, "a", encoding="utf-8") as f:
        f.write(turn)


def _append_system(chat_path: Path, body: str):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    safe_body = neutralize_agent_mentions(body).strip()
    turn = f"\n## [@system] {timestamp}\n{safe_body}\n\n---\n"
    with open(chat_path, "a", encoding="utf-8") as f:
        f.write(turn)


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


async def daemon_loop(state: AppState):
    chat_path = state.chat_path
    config = state.config
    max_chars = config["dispatch"]["max_prompt_chars"]
    auto_threshold = config["compact"]["auto_threshold_lines"]

    last_processed_header = _read_latest_turn_header(chat_path)
    pending_mentions: asyncio.Queue[str] = asyncio.Queue()
    queued_mentions: set[str] = set()

    async def watcher():
        nonlocal last_processed_header
        while True:
            try:
                if chat_path.exists():
                    text = chat_path.read_text(encoding="utf-8", errors="replace")
                    turns = _parse_turns(text)

                    start_idx = 0
                    if last_processed_header:
                        for i, t in enumerate(turns):
                            if t["header"] == last_processed_header:
                                start_idx = i + 1
                                break

                    for t in turns[start_idx:]:
                        author = t["author"]
                        if author == "you":
                            mention = find_first_mention(t["body"])
                            if mention and mention not in queued_mentions:
                                queued_mentions.add(mention)
                                await pending_mentions.put(mention)
                        elif author in ("claude", "codex", "deepseek"):
                            mention = find_tail_mention(t["body"])
                            if mention and mention not in queued_mentions:
                                queued_mentions.add(mention)
                                await pending_mentions.put(mention)
                        last_processed_header = t["header"]

            except Exception:
                logger.exception("Watcher error")

            await asyncio.sleep(1)

    async def worker():
        nonlocal last_processed_header
        while True:
            mention = await pending_mentions.get()
            await state.set_busy(mention)
            try:
                role = _get_role(config, mention)
                prompt = build_prompt(
                    mention, state.project_root, chat_path, max_chars, role=role
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
                        f"--add-dir {state.config.get('council_root', 'Council')} -p"
                    )
                else:
                    runtime = f"{binary} exec (model={model or 'CLI default'})"
                    command_hint = (
                        f"{binary} exec --dangerously-bypass-approvals-and-sandbox "
                        "--output-last-message <file> -"
                    )
                await state.add_trace(
                    mention,
                    "dispatch started",
                    f"runtime={runtime} command={command_hint} "
                    f"timeout={_format_timeout(_agent_timeout(config, mention))} "
                    f"prompt_chars={len(prompt)} "
                    f"role_chars={len(role)} "
                    f"cwd={state.project_root}",
                )

                async def trace_agent_output(source: str, text: str):
                    await state.add_trace(
                        mention,
                        f"{source}",
                        text[:2000],
                    )

                async def run_dispatch() -> DispatchResult:
                    if mention == "claude":
                        return await dispatch_claude(
                            prompt, state.project_root,
                            _agent_timeout(config, mention),
                            binary=binary, model=model, effort=effort,
                            on_output=trace_agent_output,
                        )
                    if mention == "codex":
                        return await dispatch_codex(
                            prompt, state.project_root,
                            _agent_timeout(config, mention),
                            binary=binary, model=model, effort=effort,
                            on_output=trace_agent_output,
                        )
                    if mention == "deepseek":
                        async def trace_opencode_output(source: str, text: str):
                            await state.add_trace(
                                "deepseek",
                                f"opencode {source}",
                                text[:2000],
                            )

                        return await dispatch_deepseek(
                            prompt, state.project_root,
                            _agent_timeout(config, mention),
                            model=_get_model_deepseek(config),
                            on_output=trace_opencode_output,
                        )
                    raise ValueError(f"Unknown agent: {mention}")

                dispatch_task = asyncio.create_task(run_dispatch())
                await state.set_dispatch_task(dispatch_task)
                try:
                    result = await dispatch_task
                finally:
                    await state.set_dispatch_task(None)

                response = result.text
                await state.add_trace(
                    mention,
                    "dispatch completed",
                    f"response_chars={len(response)} "
                    f"{_format_usage(result.usage)}",
                )
                _append_turn(chat_path, mention, response,
                             metadata={"tokens": result.usage})
                await state.notify_chat_update()

                if chat_path.exists():
                    text = chat_path.read_text(encoding="utf-8", errors="replace")
                    line_count = text.count("\n") + 1
                    if line_count > auto_threshold:
                        await compact_chat(chat_path, state.archive_dir, config)
                        await state.notify_chat_update()
                        last_processed_header = ""

            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():
                    raise
                await state.add_trace(mention, "dispatch cancelled", "Stopped by user.")
                _append_system(chat_path, f"agent {mention} dispatch cancelled by user.")
                await state.notify_chat_update()
            except Exception as e:
                logger.exception("Dispatch error")
                await state.add_trace(mention, "dispatch failed", str(e))
                _append_error(chat_path, mention, str(e))
                await state.notify_chat_update()
            finally:
                await state.set_idle()
                queued_mentions.discard(mention)
                pending_mentions.task_done()

    await asyncio.gather(watcher(), worker())
