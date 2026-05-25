"""Daemon: watches each session chat.md for @mentions and dispatches agents."""

import asyncio
import hashlib
import json
import re
import logging
from datetime import datetime
from pathlib import Path

from .state import (
    AGENTS,
    Session,
    SessionRegistry,
    infer_provider_for_model,
    runtime_family_for_provider,
)
from .prompt_builder import build_prompt
from .provenance import build_tool_provenance
from .agent_memory import write_agent_memory
from .dispatcher import (dispatch_claude, dispatch_codex, dispatch_deepseek,
                            dispatch_hermes,
                            DispatchResult, estimate_tokens)
from .summarizer import compact_chat
from .mentions import find_agent_mentions, neutralize_agent_mentions

logger = logging.getLogger(__name__)

TURN_HEADER_RE = re.compile(r"^##\s+\[@(\w+)\]\s+(.+)$")


def _get_provider(config: dict, agent: str) -> str:
    providers = config.get("providers", {}) or {}
    defaults = {
        "claude": "claude_cli",
        "codex": "codex_cli",
        "deepseek": "opencode",
        "hermes": "hermes_api",
    }
    configured = str(providers.get(agent) or defaults.get(agent) or "custom")
    return infer_provider_for_model(agent, configured, _get_runtime_model(config, agent))


def _get_binary(config: dict, agent: str) -> str:
    provider = _get_provider(config, agent)
    key = {
        "claude_cli": "claude",
        "codex_cli": "codex",
        "opencode": "opencode",
    }.get(provider, agent)
    return (config.get("binaries", {}).get(key)
            or config.get("models", {}).get(key)
            or key)


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


def _get_runtime_model(config: dict, agent: str) -> str:
    if agent == "deepseek":
        return _get_model_deepseek(config)
    return _get_model(config, agent)


def _get_hermes_config(config: dict) -> dict[str, str]:
    dispatch = config.get("dispatch", {}) or {}
    hermes = dispatch.get("hermes", {}) if isinstance(dispatch.get("hermes"), dict) else {}
    session_header = (
        hermes.get("session_header")
        if "session_header" in hermes
        else "X-Hermes-Session-Key"
    )
    return {
        "base_url": str(hermes.get("base_url") or "http://127.0.0.1:8642/v1"),
        "session_key": str(hermes.get("session_key") or ""),
        "session_header": str(session_header or ""),
    }


def _agent_env(config: dict, agent: str) -> dict[str, str]:
    api_keys = config.get("api_keys", {}) or {}
    provider = _get_provider(config, agent)
    env: dict[str, str] = {}
    if api_keys.get("openrouter"):
        env["OPENROUTER_API_KEY"] = str(api_keys["openrouter"])
    if api_keys.get("deepseek"):
        env["DEEPSEEK_API_KEY"] = str(api_keys["deepseek"])
    if api_keys.get("deepseek_flash"):
        env["DEEPSEEK_FLASH_API_KEY"] = str(api_keys["deepseek_flash"])
    if provider == "claude_cli":
        key = api_keys.get(agent) or api_keys.get("claude")
        if key:
            env["ANTHROPIC_API_KEY"] = str(key)
    if provider == "codex_cli":
        key = api_keys.get(agent) or api_keys.get("codex")
        if key:
            env["OPENAI_API_KEY"] = str(key)
    if provider == "opencode":
        key = api_keys.get(agent) or api_keys.get("deepseek")
        if key:
            env["OPENCODE_DEEPSEEK_API_KEY"] = str(key)
    if provider:
        env["COUNCIL_AGENT_PROVIDER"] = provider
    return env


def _api_key_for(config: dict, agent: str) -> str:
    api_keys = config.get("api_keys", {}) or {}
    provider = _get_provider(config, agent)
    if provider == "openrouter":
        return str(api_keys.get("openrouter") or api_keys.get(agent) or "")
    if provider == "deepseek_api":
        return str(api_keys.get("deepseek") or api_keys.get(agent) or "")
    return str(api_keys.get(agent) or "")


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


def _request_id(header: str, agent: str) -> str:
    digest = hashlib.sha1(_mention_key(header, agent).encode("utf-8")).hexdigest()[:12]
    return f"{agent}:{digest}"


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


def _turn_mentions(turn: dict, aliases: dict | None = None) -> list[str]:
    author = turn["author"]
    if author == "you" or author in AGENTS:
        return find_agent_mentions(turn["body"], aliases)
    return []


def _chained_mentions(author: str, body: str, aliases: dict | None = None) -> list[str]:
    return [
        mention
        for mention in _turn_mentions({"author": author, "body": body}, aliases)
        if mention != author
    ]


def _request_still_valid(
    chat_path: Path,
    request: dict[str, str],
    aliases: dict | None = None,
) -> bool:
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
        if source == "agent" and author not in AGENTS:
            continue
        return request["agent"] in _turn_mentions(turn, aliases)
    return False


def _seed_dispatch_ledger(
    turns: list[dict],
    dispatched_mentions: set[str],
    aliases: dict | None = None,
) -> bool:
    changed = False
    for i, turn in enumerate(turns):
        for agent in _turn_mentions(turn, aliases):
            if turn["author"] == "you" and not _has_later_response(turns, i):
                continue
            key = _mention_key(turn["header"], agent)
            if key not in dispatched_mentions:
                dispatched_mentions.add(key)
                changed = True
    return changed


def _has_later_response(turns: list[dict], turn_index: int) -> bool:
    for later in turns[turn_index + 1:]:
        if later["author"] in (*AGENTS, "system"):
            return True
    return False


def _prune_unresolved_user_mentions(
    turns: list[dict],
    dispatched_mentions: set[str],
    aliases: dict | None = None,
) -> bool:
    changed = False
    for i, turn in enumerate(turns):
        if turn["author"] != "you" or _has_later_response(turns, i):
            continue
        for agent in _turn_mentions(turn, aliases):
            key = _mention_key(turn["header"], agent)
            if key in dispatched_mentions:
                dispatched_mentions.remove(key)
                changed = True
    return changed


def _initial_processed_header(
    turns: list[dict],
    dispatched_mentions: set[str],
    aliases: dict | None = None,
) -> str:
    for i in range(len(turns) - 1, -1, -1):
        turn = turns[i]
        if turn["author"] != "you" or _has_later_response(turns, i):
            continue
        if any(
            _mention_key(turn["header"], agent) not in dispatched_mentions
            for agent in _turn_mentions(turn, aliases)
        ):
            return turns[i - 1]["header"] if i > 0 else ""
    return turns[-1]["header"] if turns else ""


def _load_turn_dispatch_modes(events_path: Path) -> dict[str, str]:
    modes: dict[str, str] = {}
    if not events_path.exists():
        return modes
    for line in events_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("kind") != "user_turn":
            continue
        metadata = event.get("metadata") or {}
        mode = metadata.get("dispatch_mode") or "parallel"
        if mode not in ("parallel", "queued"):
            mode = "parallel"
        author = event.get("author") or "you"
        display_ts = event.get("display_ts") or ""
        if display_ts:
            modes[f"## [@{author}] {display_ts}"] = mode
    return modes


def _agent_timeout(config: dict, agent: str) -> int:
    dispatch = config.get("dispatch", {})
    default_timeout = int(dispatch.get("timeout_seconds", 300))
    return int(dispatch.get(f"{agent}_timeout_seconds", default_timeout))


def _agent_attachment_policy(config: dict, agent: str) -> str:
    attachments = config.get("dispatch", {}).get("attachments", {})
    if not isinstance(attachments, dict):
        return ""
    return str(attachments.get(agent, ""))


def _config_bool(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _agent_prompt_profile(config: dict, agent: str, runtime_family: str) -> dict:
    dispatch = config.get("dispatch", {}) or {}
    profile = {
        "max_chars": int(dispatch.get("max_prompt_chars", 25000)),
        "min_chat_tail_turns": max(
            8,
            int(config.get("compact", {}).get("verbatim_tail_turns", 8)),
        ),
        "include_agent_memory": True,
        "include_recent_actions": True,
        "project_rules_max_chars": None,
        "compaction_summary_max_chars": None,
    }
    if runtime_family == "hermes":
        profile.update({
            "max_chars": int(
                dispatch.get(
                    "council_injection_max_chars",
                    dispatch.get("hermes_max_prompt_chars", 6000),
                )
            ),
            "min_chat_tail_turns": int(dispatch.get("hermes_min_chat_tail_turns", 2)),
            "include_agent_memory": _config_bool(
                dispatch.get("hermes_include_agent_memory"), False
            ),
            "include_recent_actions": _config_bool(
                dispatch.get("hermes_include_recent_actions"), False
            ),
            "project_rules_max_chars": int(
                dispatch.get("hermes_project_rules_max_chars", 1500)
            ),
            "compaction_summary_max_chars": int(
                dispatch.get("hermes_compaction_summary_max_chars", 1500)
            ),
        })
    return profile


def _merge_alias_snapshots(*snapshots: dict | None) -> dict[str, list[str]]:
    merged: dict[str, list[str]] = {}
    for snapshot in snapshots:
        if not isinstance(snapshot, dict):
            continue
        for agent in AGENTS:
            alias = str(snapshot.get(agent) or "").strip().lstrip("@").lower()
            if alias and alias not in merged.setdefault(agent, []):
                merged[agent].append(alias)
    return merged


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


def _display_path(path: Path | None, root: Path | None) -> str:
    if path is None:
        return "none"
    if root is None:
        return str(path)
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


async def session_daemon_loop(session: Session, registry: SessionRegistry):
    chat_path = session.chat_path

    dispatch_ledger_path = session.session_dir / "dispatch-ledger.json"
    dispatched_mentions = _load_dispatch_ledger(dispatch_ledger_path)
    existing_turns: list[dict] = []
    if chat_path.exists():
        startup_aliases = session.effective_config().get("aliases", {})
        text = chat_path.read_text(encoding="utf-8", errors="replace")
        existing_turns = _parse_turns(text)
        changed = False
        if _prune_unresolved_user_mentions(existing_turns, dispatched_mentions, startup_aliases):
            changed = True
        if not dispatched_mentions and _seed_dispatch_ledger(existing_turns, dispatched_mentions, startup_aliases):
            changed = True
        if changed:
            _save_dispatch_ledger(dispatch_ledger_path, dispatched_mentions)
    last_processed_header = _initial_processed_header(
        existing_turns,
        dispatched_mentions,
        session.effective_config().get("aliases", {}),
    )
    pending_mentions = session.dispatch_queue
    pending_changed = asyncio.Condition()
    await session.set_dispatch_queue(pending_mentions)

    async def enqueue_mention(
        agent: str,
        source: str,
        header: str,
        dispatch_mode: str = "parallel",
    ) -> None:
        nonlocal pending_mentions

        if source != "user" and session.current_agent == agent:
            return
        key = _mention_key(header, agent)
        if key in dispatched_mentions:
            await session.add_trace(
                agent,
                "dispatch skipped",
                f"Already handled mention in {header}.",
            )
            return
        dispatched_mentions.add(key)
        _save_dispatch_ledger(dispatch_ledger_path, dispatched_mentions)

        request = {
            "id": _request_id(header, agent),
            "agent": agent,
            "source": source,
            "header": header,
            "mode": dispatch_mode,
            "aliases": session.effective_config().get("aliases", {}).copy(),
        }
        await session.add_trace(
            agent,
            "dispatch queued",
            f"mode={dispatch_mode} source={source} header={header}",
        )
        if dispatch_mode == "parallel":
            asyncio.create_task(process_dispatch_request(request))
            await session.broadcast_status()
            return

        async with pending_changed:
            pending_mentions.append(request)
            await session.broadcast_status()
            pending_changed.notify()

    async def next_mention() -> dict[str, str]:
        async with pending_changed:
            while not pending_mentions:
                await pending_changed.wait()
            request = pending_mentions.pop(0)
            await session.broadcast_status()
            return request

    async def launch_promoted_mentions() -> None:
        launched: list[dict[str, str]] = []
        async with pending_changed:
            i = 0
            while i < len(pending_mentions):
                request = pending_mentions[i]
                if request.get("mode") == "parallel" and request.get("promoted"):
                    launched.append(pending_mentions.pop(i))
                    continue
                i += 1
            if launched:
                await session.broadcast_status()
        for request in launched:
            await session.add_trace(
                request.get("agent") or "system",
                "dispatch promoted",
                f"header={request.get('header', '')}",
            )
            asyncio.create_task(process_dispatch_request(request))

    async def watcher():
        nonlocal last_processed_header
        while True:
            try:
                await launch_promoted_mentions()
                if chat_path.exists():
                    text = chat_path.read_text(encoding="utf-8", errors="replace")
                    turns = _parse_turns(text)
                    dispatch_modes = _load_turn_dispatch_modes(session.events_path)
                    aliases = session.effective_config().get("aliases", {})

                    start_idx = 0
                    if last_processed_header:
                        header_idx = _find_header_index(turns, last_processed_header)
                        if header_idx is None:
                            if _seed_dispatch_ledger(turns, dispatched_mentions, aliases):
                                _save_dispatch_ledger(
                                    dispatch_ledger_path,
                                    dispatched_mentions,
                                )
                            last_processed_header = turns[-1]["header"] if turns else ""
                            await session.add_trace(
                                "system",
                                "chat rewrite detected",
                                "Dispatch cursor resynced without replaying old mentions.",
                            )
                            await asyncio.sleep(1)
                            continue
                        else:
                            start_idx = header_idx + 1

                    for turn in turns[start_idx:]:
                        author = turn["author"]
                        source = None
                        if author == "you":
                            source = "user"
                        elif author in AGENTS:
                            source = "agent"
                        if source:
                            for mention in _turn_mentions(turn, aliases):
                                if source == "agent" and author == mention:
                                    continue
                                mode = dispatch_modes.get(turn["header"], "parallel")
                                if source != "user":
                                    mode = "queued"
                                await enqueue_mention(
                                    mention,
                                    source,
                                    turn["header"],
                                    mode,
                                )
                        last_processed_header = turn["header"]

            except Exception:
                logger.exception("Watcher error")

            await asyncio.sleep(1)

    consecutive_agent_dispatches = 0

    async def process_dispatch_request(request: dict[str, str]) -> None:
        nonlocal last_processed_header
        nonlocal consecutive_agent_dispatches
        config = session.effective_config()
        aliases = _merge_alias_snapshots(config.get("aliases", {}), request.get("aliases"))
        if not _request_still_valid(chat_path, request, aliases):
            await session.add_trace(
                request.get("agent") or "system",
                "dispatch skipped",
                f"Mention no longer exists in {request.get('header', 'unknown turn')}.",
            )
            return
        mention = request["agent"]
        chain_depth_limit = int(config.get("dispatch", {}).get("chain_depth_limit", 3))
        if request["source"] == "user":
            consecutive_agent_dispatches = 0
        else:
            consecutive_agent_dispatches += 1
            if consecutive_agent_dispatches > chain_depth_limit:
                safe_body = neutralize_agent_mentions(
                    f"Chain depth limit ({chain_depth_limit}) reached. "
                    f"Dispatch of agent {mention} was suppressed to prevent "
                    f"an infinite agent-agent loop. User intervention required."
                )
                session.append_turn("system", safe_body, kind="system_turn")
                await session.notify_chat_update()
                return
        auto_threshold = config["compact"]["auto_threshold_lines"]
        captured_output_parts: list[str] = []
        dispatch_trace_events: list[dict] = []
        dispatch_task: asyncio.Task | None = None
        reserved_turn = False
        response_header = ""
        working_root = session.working_root
        try:
            role = _get_role(config, mention)
            provider = _get_provider(config, mention)
            runtime_family = runtime_family_for_provider(provider, mention)
            prompt_profile = _agent_prompt_profile(config, mention, runtime_family)
            max_chars = prompt_profile["max_chars"]
            prompt = build_prompt(
                mention,
                session.project_root,
                chat_path,
                max_chars,
                role=role,
                session_dir=session.session_dir,
                aliases=config.get("aliases", {}),
                session_name=session.name,
                compactions_path=session.compactions_path,
                attachment_policy=_agent_attachment_policy(config, mention),
                min_chat_tail_turns=prompt_profile["min_chat_tail_turns"],
                include_agent_memory=prompt_profile["include_agent_memory"],
                include_recent_actions=prompt_profile["include_recent_actions"],
                project_rules_max_chars=prompt_profile["project_rules_max_chars"],
                compaction_summary_max_chars=prompt_profile["compaction_summary_max_chars"],
                council_context_hint=runtime_family == "hermes",
            )
            binary = _get_binary(config, mention)
            model = _get_runtime_model(config, mention)
            effort = _get_effort(config, mention)
            agent_env = _agent_env(config, mention)
            if runtime_family == "deepseek":
                runtime = model
                command_hint = (
                    f"opencode run -m {runtime} "
                    "--dangerously-skip-permissions <instruction> "
                    "--file=<prompt.md>"
                )
            elif runtime_family == "claude":
                runtime = f"{binary} CLI (model={model or 'CLI default'})"
                command_hint = (
                    f"{binary} --dangerously-skip-permissions "
                    f"--add-dir {registry.council_root} -p"
                )
            elif runtime_family == "codex":
                runtime = f"{binary} exec (model={model or 'CLI default'})"
                command_hint = (
                    f"{binary} exec --dangerously-bypass-approvals-and-sandbox "
                    "--output-last-message <file> -"
                )
            else:
                hermes_cfg = _get_hermes_config(config)
                runtime = f"{provider} (model={model or 'hermes-agent'})"
                header = hermes_cfg["session_header"] or "no session header"
                command_hint = (
                    f"POST {hermes_cfg['base_url'].rstrip('/')}/chat/completions "
                    f"with {header}"
                )
            await session.add_trace(
                mention,
                "dispatch started",
                f"mode={request.get('mode', 'parallel')} "
                f"runtime={runtime} command={command_hint} "
                f"timeout={_format_timeout(_agent_timeout(config, mention))} "
                f"prompt_chars={len(prompt)} "
                f"prompt_tokens_est={estimate_tokens(prompt)} "
                f"role_chars={len(role)} "
                f"cwd={working_root}",
            )
            response_header = session.reserve_agent_turn(
                request["id"],
                mention,
                "Dispatch started. Waiting for the agent's final response.",
                metadata={
                    "dispatch_mode": request.get("mode", "parallel"),
                    "prompt_tokens_est": estimate_tokens(prompt),
                    "tool_calls": [],
                },
            )
            reserved_turn = True
            await session.notify_chat_update()

            async def trace_agent_output(source: str, text: str):
                captured_output_parts.append(f"[{source}]\n{text}")
                dispatch_trace_events.append({
                    "agent": mention,
                    "message": source,
                    "detail": text,
                })
                await session.add_trace(
                    mention,
                    f"{source}",
                    text,
                )
                if reserved_turn:
                    session.update_reserved_agent_turn(
                        request["id"],
                        metadata={
                            "tool_calls": build_tool_provenance(dispatch_trace_events),
                        },
                    )
                    await session.notify_chat_update()

            async def run_dispatch() -> DispatchResult:
                if runtime_family == "claude":
                    return await dispatch_claude(
                        prompt, working_root,
                        _agent_timeout(config, mention),
                        binary=binary, model=model, effort=effort,
                        on_output=trace_agent_output,
                        env=agent_env,
                    )
                if runtime_family == "codex":
                    return await dispatch_codex(
                        prompt, working_root,
                        _agent_timeout(config, mention),
                        binary=binary, model=model, effort=effort,
                        on_output=trace_agent_output,
                        env=agent_env,
                    )
                if runtime_family == "deepseek":
                    async def trace_opencode_output(source: str, text: str):
                        captured_output_parts.append(f"[opencode {source}]\n{text}")
                        dispatch_trace_events.append({
                            "agent": "deepseek",
                            "message": f"opencode {source}",
                            "detail": text,
                        })
                        await session.add_trace(
                            "deepseek",
                            f"opencode {source}",
                            text,
                        )

                    return await dispatch_deepseek(
                        prompt, working_root,
                        _agent_timeout(config, mention),
                        model=model,
                        effort=effort,
                        on_output=trace_opencode_output,
                        env=agent_env,
                    )
                if runtime_family == "hermes":
                    hermes_cfg = _get_hermes_config(config)
                    return await dispatch_hermes(
                        prompt,
                        working_root,
                        _agent_timeout(config, mention),
                        model=model or "hermes-agent",
                        base_url=hermes_cfg["base_url"],
                        api_key=_api_key_for(config, mention),
                        session_key=hermes_cfg["session_key"],
                        session_header=hermes_cfg["session_header"],
                        on_output=trace_agent_output,
                    )
                raise ValueError(f"Unknown agent: {mention}")

            dispatch_task = asyncio.create_task(run_dispatch())
            await session.start_dispatch(request, dispatch_task)
            try:
                result = await dispatch_task
            finally:
                await session.finish_dispatch(request["id"])

            response = result.text
            if not response.strip():
                raw_output = "\n\n".join(captured_output_parts).strip()
                if raw_output:
                    raise RuntimeError(
                        "agent exited without a final chat response; "
                        "captured CLI output was saved to agent memory"
                    )
                raise RuntimeError("agent exited without producing output")
            artifact_path = write_agent_memory(
                session.session_dir,
                mention,
                "completed",
                final_response=response,
                captured_output="\n\n".join(captured_output_parts),
                usage=result.usage,
                project_root=session.project_root,
            )
            await session.add_trace(
                mention,
                "dispatch completed",
                f"response_chars={len(response)} "
                f"response_tokens_est={estimate_tokens(response)} "
                f"{_format_usage(result.usage)} "
                f"memory={_display_path(artifact_path, session.working_root)}",
            )
            if reserved_turn:
                session.update_reserved_agent_turn(
                    request["id"],
                    text=response,
                    usage=result.usage,
                    status="completed",
                    metadata={
                        "dispatch_mode": request.get("mode", "parallel"),
                        "prompt_tokens_est": estimate_tokens(prompt),
                        "response_tokens_est": estimate_tokens(response),
                        "tool_calls": build_tool_provenance(dispatch_trace_events),
                    },
                )
            else:
                response_header = session.append_turn(
                    mention, response,
                    usage=result.usage,
                    metadata={
                        "dispatch_mode": request.get("mode", "parallel"),
                        "prompt_tokens_est": estimate_tokens(prompt),
                        "response_tokens_est": estimate_tokens(response),
                        "tool_calls": build_tool_provenance(dispatch_trace_events),
                    },
                )
            if response_header:
                response_aliases = _merge_alias_snapshots(
                    session.effective_config().get("aliases", {}),
                    request.get("aliases"),
                )
                for chained_mention in _chained_mentions(
                    mention,
                    response,
                    response_aliases,
                ):
                    await enqueue_mention(
                        chained_mention,
                        "agent",
                        response_header,
                        "queued",
                    )
            await session.notify_chat_update()

            if chat_path.exists():
                text = chat_path.read_text(encoding="utf-8", errors="replace")
                line_count = text.count("\n") + 1
                if (
                    line_count > auto_threshold
                    and not session.active_dispatches
                    and not pending_mentions
                ):
                    await session.add_trace(
                        "system",
                        "auto compact started",
                        f"chat lines={line_count} threshold={auto_threshold}",
                    )
                    await session.set_compacting()
                    try:
                        await compact_chat(session, config)
                    finally:
                        await session.set_idle()
                    await session.notify_chat_update()
                    last_processed_header = ""
                elif line_count > auto_threshold:
                    await session.add_trace(
                        "system",
                        "auto compact deferred",
                        "Waiting for active and queued agents to finish.",
                    )

        except asyncio.CancelledError:
            if dispatch_task is None or not dispatch_task.cancelled():
                raise
            artifact_path = write_agent_memory(
                session.session_dir,
                mention,
                "cancelled",
                captured_output="\n\n".join(captured_output_parts),
                error="Stopped by user.",
                project_root=session.project_root,
            )
            await session.add_trace(mention, "dispatch cancelled", "Stopped by user.")
            if reserved_turn:
                session.update_reserved_agent_turn(
                    request["id"],
                    text="Dispatch cancelled by user before a final response.",
                    status="cancelled",
                    metadata={
                        "dispatch_mode": request.get("mode", "parallel"),
                        "tool_calls": build_tool_provenance(dispatch_trace_events),
                    },
                )
            safe_body = neutralize_agent_mentions(
                f"agent {mention} dispatch cancelled by user. Partial effort saved for @{mention}: "
                f"`{_display_path(artifact_path, session.working_root)}`"
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
                project_root=session.project_root,
            )
            await session.add_trace(mention, "dispatch failed", str(e))
            safe_reason = neutralize_agent_mentions(str(e)).strip()
            if reserved_turn:
                session.update_reserved_agent_turn(
                    request["id"],
                    text=f"Dispatch failed before a final response: {safe_reason}",
                    status="failed",
                    metadata={
                        "dispatch_mode": request.get("mode", "parallel"),
                        "tool_calls": build_tool_provenance(dispatch_trace_events),
                    },
                )
            session.append_turn(
                "system",
                f"error: agent {mention} dispatch failed: {safe_reason}\n\n"
                f"Partial effort saved for @{mention}: "
                f"`{_display_path(artifact_path, session.working_root)}`",
                kind="system_turn",
            )
            await session.notify_chat_update()
        finally:
            await session.finish_dispatch(request["id"])

    async def worker():
        while True:
            while session.active_dispatches:
                await asyncio.sleep(0.25)
            request = await next_mention()
            await process_dispatch_request(request)

    await asyncio.gather(watcher(), worker())
