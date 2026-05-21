"""Dispatcher: subprocess wrappers for claude, codex, opencode."""

import asyncio
import re
import shutil
import logging
import tempfile
import sys
import os
import json
import hashlib
import urllib.error
import urllib.request
from pathlib import Path
from dataclasses import dataclass
from collections.abc import Awaitable, Callable
from urllib.parse import urljoin, urlparse

logger = logging.getLogger(__name__)


ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
OPENCODE_HEADER_RE = re.compile(r"^>\s+\S+\s+.*deepseek", re.IGNORECASE)
OPENCODE_FILE_DONE_RE = re.compile(
    r"^Done\..*Written to .*(?:council|prompt).*\.(?:md|txt)(?:\.compacted)?\.?$",
    re.IGNORECASE,
)
TASKKILL_SUCCESS_RE = re.compile(
    r"^SUCCESS:\s+The process with PID \d+ "
    r"\(child process of PID \d+\) has been terminated\.\s*$"
)
THINKING_LINE_RE = re.compile(r"^\s*(Thinking|Thoughts?|Reasoning):\s*", re.IGNORECASE)
OPENCODE_PROGRESS_LINE_RE = re.compile(
    r"^\s*(?:"
    r"Let me\b|"
    r"Now\b|"
    r"Good[,.]?\b|"
    r"I need to\b|"
    r"All changes are implemented and verified\b"
    r")",
    re.IGNORECASE,
)
OPENCODE_FINAL_LINE_RE = re.compile(
    r"^\s*(?:"
    r"Done\.|"
    r"Implemented\b|"
    r"Fixed\b|"
    r"Added\b|"
    r"Artifact produced\b|"
    r"Diagnostics added\b|"
    r"Bang,"
    r")",
    re.IGNORECASE,
)
COUNCIL_ROOT = Path(__file__).resolve().parent.parent
USAGE_PATTERNS = {
    "prompt_tokens": [
        re.compile(r'"prompt_tokens"\s*:\s*(\d[\d,]*)', re.IGNORECASE),
        re.compile(r'"input_tokens"\s*:\s*(\d[\d,]*)', re.IGNORECASE),
        re.compile(r"\b(?:prompt|input)[\s_-]+tokens?\s*[:=]\s*(\d[\d,]*)", re.IGNORECASE),
    ],
    "completion_tokens": [
        re.compile(r'"completion_tokens"\s*:\s*(\d[\d,]*)', re.IGNORECASE),
        re.compile(r'"output_tokens"\s*:\s*(\d[\d,]*)', re.IGNORECASE),
        re.compile(r"\b(?:completion|output|response)[\s_-]+tokens?\s*[:=]\s*(\d[\d,]*)", re.IGNORECASE),
    ],
    "total_tokens": [
        re.compile(r'"total_tokens"\s*:\s*(\d[\d,]*)', re.IGNORECASE),
        re.compile(r"\btotal[\s_-]+tokens?\s*[:=]\s*(\d[\d,]*)", re.IGNORECASE),
    ],
}


@dataclass
class DispatchResult:
    text: str
    usage: dict[str, int]


class SubprocessTimeoutError(RuntimeError):
    """Raised when an agent CLI exceeds its Council timeout."""


def _resolve(name: str) -> str:
    """Resolve a command name to a full path so subprocess can find .cmd/.bat
    shims on Windows (asyncio.create_subprocess_exec does NOT consult PATHEXT)."""
    resolved = shutil.which(name)
    if not resolved:
        raise RuntimeError(
            f"command '{name}' not found on PATH. "
            f"Install it or check your shell environment."
        )
    return resolved


def _strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def _strip_opencode_header(text: str) -> str:
    """Strip opencode's own header line like '> build · deepseek-v4-pro'."""
    lines = [
        line for line in text.splitlines()
        if not OPENCODE_HEADER_RE.match(line.strip())
    ]
    return "\n".join(lines)


def _opencode_stdout_is_file_status(text: str) -> bool:
    cleaned = _strip_opencode_header(_strip_ansi(text))
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    return bool(lines) and all(OPENCODE_FILE_DONE_RE.match(line) for line in lines)


def _read_opencode_file_output(
    stdout: str,
    prompt_path: Path,
    original_body: str,
) -> str:
    """Return file output when opencode writes there instead of stdout."""
    if not _opencode_stdout_is_file_status(stdout):
        return stdout

    candidates = [Path(str(prompt_path) + ".compacted"), prompt_path]
    for candidate in candidates:
        if not candidate.exists():
            continue
        content = candidate.read_text(encoding="utf-8", errors="replace").strip()
        if content and content != original_body.strip():
            return content
    return stdout


def _sanitize_agent_output(text: str, strip_thinking: bool = False) -> str:
    """Remove CLI/process-manager noise that is not an agent response."""
    cleaned = _strip_ansi(text)
    lines = []
    skipping_thinking = False
    for line in cleaned.splitlines():
        stripped = line.strip()
        if TASKKILL_SUCCESS_RE.match(stripped):
            continue
        if strip_thinking and THINKING_LINE_RE.match(stripped):
            skipping_thinking = True
            continue
        if skipping_thinking:
            if not stripped:
                skipping_thinking = False
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _trim_opencode_progress_output(text: str) -> str:
    """Keep opencode progress chatter in trace, not in the final chat turn."""
    lines = text.splitlines()
    first_content_idx = next(
        (idx for idx, line in enumerate(lines) if line.strip()),
        None,
    )
    if first_content_idx is None:
        return text.strip()
    if not OPENCODE_PROGRESS_LINE_RE.match(lines[first_content_idx].strip()):
        return text.strip()

    final_idx = None
    for idx in range(first_content_idx + 1, len(lines)):
        if OPENCODE_FINAL_LINE_RE.match(lines[idx].strip()):
            final_idx = idx
            break
    if final_idx is None:
        return text.strip()
    return "\n".join(lines[final_idx:]).strip()


def _preview_output(stdout: str, stderr: str, max_chars: int = 1200) -> str:
    parts = []
    stdout_clean = _sanitize_agent_output(stdout)
    stderr_clean = _sanitize_agent_output(stderr)
    if stdout_clean:
        parts.append(f"last stdout: {stdout_clean[-max_chars:]}")
    if stderr_clean:
        parts.append(f"last stderr: {stderr_clean[-max_chars:]}")
    return " | ".join(parts)


def _timeout_enabled(timeout: int | float | None) -> bool:
    return timeout is not None and timeout > 0


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _parse_int(value: str) -> int:
    return int(value.replace(",", ""))


def parse_token_usage(text: str) -> dict[str, int]:
    usage: dict[str, int] = {}
    cleaned = _strip_ansi(text)
    for key, patterns in USAGE_PATTERNS.items():
        for pattern in patterns:
            match = pattern.search(cleaned)
            if match:
                usage[key] = _parse_int(match.group(1))
                break
    if (
        "total_tokens" not in usage
        and "prompt_tokens" in usage
        and "completion_tokens" in usage
    ):
        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
    return usage


def _merge_usage(*texts: str) -> dict[str, int]:
    usage: dict[str, int] = {}
    for text in texts:
        usage.update(parse_token_usage(text))
    return usage


async def _run_opencode_with_prompt_file(
    prompt: str,
    project_root: Path,
    timeout: int,
    model: str,
    instruction: str,
    on_output: Callable[[str, str], Awaitable[None]] | None = None,
    env: dict[str, str] | None = None,
) -> str:
    file_body = f"{instruction}\n\n{prompt}"
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".md",
        prefix="council-prompt-",
        delete=False,
    ) as prompt_file:
        prompt_file.write(file_body)
        prompt_path = Path(prompt_file.name)

    try:
        stdout, _stderr = await _run_subprocess_streaming(
            [_resolve("opencode"), "run", "-m", model,
             "--dangerously-skip-permissions", instruction,
             f"--file={prompt_path}"],
            project_root,
            timeout,
            on_output=on_output,
            env=env,
        )
        return _read_opencode_file_output(stdout, prompt_path, file_body)
    finally:
        prompt_path.unlink(missing_ok=True)
        Path(str(prompt_path) + ".compacted").unlink(missing_ok=True)


async def _read_stream(
    stream: asyncio.StreamReader,
    parts: list[str],
    source: str,
    on_output: Callable[[str, str], Awaitable[None]] | None,
) -> None:
    while True:
        chunk = await stream.read(4096)
        if not chunk:
            break
        text = chunk.decode("utf-8", errors="replace")
        parts.append(text)
        if on_output:
            visible = _strip_ansi(text).strip()
            if visible:
                await on_output(source, visible)


async def _run_subprocess(
    cmd: list[str],
    cwd: Path,
    timeout: int,
    input_text: str | None = None,
    on_output: Callable[[str, str], Awaitable[None]] | None = None,
    env: dict[str, str] | None = None,
) -> tuple[str, str]:
    if on_output is not None:
        return await _run_subprocess_streaming(
            cmd,
            cwd,
            timeout,
            input_text=input_text,
            on_output=on_output,
            env=env,
        )

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE if input_text is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        env={**os.environ, **(env or {})},
    )
    try:
        input_bytes = (
            input_text.encode("utf-8") if input_text is not None else None
        )
        if _timeout_enabled(timeout):
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input_bytes), timeout=timeout
            )
        else:
            stdout, stderr = await proc.communicate(input_bytes)
    except asyncio.CancelledError:
        await _kill_process_tree(proc)
        raise
    except asyncio.TimeoutError:
        await _kill_process_tree(proc)
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=2
            )
        except Exception:
            stdout, stderr = b"", b""
        detail = _preview_output(
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
        )
        suffix = f"; {detail}" if detail else ""
        raise SubprocessTimeoutError(
            f"subprocess timed out after {timeout}s{suffix}"
        )

    stdout_str = stdout.decode("utf-8", errors="replace")
    stderr_str = stderr.decode("utf-8", errors="replace")

    if proc.returncode != 0 and stderr_str.strip():
        stderr_clean = _strip_ansi(stderr_str).strip()
        raise RuntimeError(
            f"subprocess exited {proc.returncode}: {stderr_clean}"
        )

    return stdout_str, stderr_str


async def _kill_process_tree(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return

    if sys.platform == "win32":
        killer = await asyncio.create_subprocess_exec(
            "taskkill", "/PID", str(proc.pid), "/T", "/F",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await killer.communicate()
    else:
        proc.kill()

    try:
        await asyncio.wait_for(proc.wait(), timeout=5)
    except asyncio.TimeoutError:
        proc.kill()


async def _write_stdin(
    stdin: asyncio.StreamWriter | None,
    input_text: str | None,
) -> None:
    if stdin is None:
        return
    try:
        if input_text is not None:
            stdin.write(input_text.encode("utf-8"))
            await stdin.drain()
        stdin.close()
        await stdin.wait_closed()
    except (BrokenPipeError, ConnectionResetError):
        pass
    finally:
        if not stdin.is_closing():
            stdin.close()
            try:
                await stdin.wait_closed()
            except Exception:
                pass


async def _run_subprocess_streaming(
    cmd: list[str],
    cwd: Path,
    timeout: int,
    input_text: str | None = None,
    on_output: Callable[[str, str], Awaitable[None]] | None = None,
    env: dict[str, str] | None = None,
) -> tuple[str, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE if input_text is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        env={**os.environ, **(env or {})},
    )

    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    stdin_task = asyncio.create_task(_write_stdin(proc.stdin, input_text))
    stdout_task = asyncio.create_task(
        _read_stream(proc.stdout, stdout_parts, "stdout", on_output)
    )
    stderr_task = asyncio.create_task(
        _read_stream(proc.stderr, stderr_parts, "stderr", on_output)
    )

    try:
        if _timeout_enabled(timeout):
            await asyncio.wait_for(proc.wait(), timeout=timeout)
        else:
            await proc.wait()
        await asyncio.gather(stdin_task, stdout_task, stderr_task)
    except asyncio.CancelledError:
        await _kill_process_tree(proc)
        for task in (stdin_task, stdout_task, stderr_task):
            task.cancel()
        await asyncio.gather(stdin_task, stdout_task, stderr_task, return_exceptions=True)
        raise
    except asyncio.TimeoutError:
        await _kill_process_tree(proc)
        for task in (stdin_task, stdout_task, stderr_task):
            task.cancel()
        await asyncio.gather(stdin_task, stdout_task, stderr_task, return_exceptions=True)
        stdout_str = "".join(stdout_parts)
        stderr_str = "".join(stderr_parts)
        detail = _preview_output(stdout_str, stderr_str)
        suffix = f"; {detail}" if detail else ""
        raise SubprocessTimeoutError(
            f"subprocess timed out after {timeout}s{suffix}"
        )

    stdout_str = "".join(stdout_parts)
    stderr_str = "".join(stderr_parts)

    if proc.returncode != 0 and stderr_str.strip():
        stderr_clean = _strip_ansi(stderr_str).strip()
        raise RuntimeError(
            f"subprocess exited {proc.returncode}: {stderr_clean}"
        )

    return stdout_str, stderr_str


async def dispatch_claude(prompt: str, project_root: Path,
                          timeout: int = 300,
                          binary: str = "claude",
                          model: str = "",
                          effort: str = "",
                          on_output: Callable[[str, str], Awaitable[None]] | None = None,
                          env: dict[str, str] | None = None) -> DispatchResult:
    argv = [_resolve(binary), "--dangerously-skip-permissions",
            "--no-session-persistence", "--add-dir", str(COUNCIL_ROOT)]
    if model:
        argv.extend(["--model", model])
    if effort:
        argv.extend(["--effort", effort])
    argv.append("-p")
    stdout, stderr = await _run_subprocess(
        argv,
        project_root,
        timeout,
        input_text=prompt,
        on_output=on_output,
        env=env,
    )
    text = _sanitize_agent_output(stdout)
    return DispatchResult(text=text, usage=_merge_usage(stdout, stderr, text))


async def dispatch_codex(prompt: str, project_root: Path,
                         timeout: int = 300,
                         binary: str = "codex",
                         model: str = "",
                         effort: str = "",
                         on_output: Callable[[str, str], Awaitable[None]] | None = None,
                         env: dict[str, str] | None = None) -> DispatchResult:
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".md",
        prefix="council-codex-last-",
        delete=False,
    ) as output_file:
        output_path = Path(output_file.name)

    try:
        argv = [_resolve(binary), "exec",
                "--dangerously-bypass-approvals-and-sandbox",
                "--add-dir", str(COUNCIL_ROOT)]
        if model:
            argv.extend(["--model", model])
        if effort:
            argv.extend(["-c", f"model_reasoning_effort={effort}"])
        argv.extend(["--output-last-message", str(output_path), "-"])
        stdout, stderr = await _run_subprocess(
            argv,
            project_root,
            timeout,
            input_text=prompt,
            on_output=on_output,
            env=env,
        )
        usage = _merge_usage(stdout, stderr)
        if output_path.exists():
            final_message = output_path.read_text(
                encoding="utf-8", errors="replace"
            ).strip()
            if final_message:
                text = _sanitize_agent_output(final_message)
                usage.update(_merge_usage(final_message, text))
                return DispatchResult(text=text, usage=usage)
        text = _sanitize_agent_output(stdout)
        usage.update(_merge_usage(text))
        return DispatchResult(text=text, usage=usage)
    finally:
        output_path.unlink(missing_ok=True)


async def dispatch_deepseek(prompt: str, project_root: Path,
                            timeout: int = 300,
                            model: str = "deepseek/deepseek-v4-pro",
                            effort: str = "",
                            on_output: Callable[[str, str], Awaitable[None]] | None = None,
                            env: dict[str, str] | None = None) -> DispatchResult:
    # TODO: deepseek reasoning effort is not yet passed to opencode CLI.
    # opencode may support effort via model suffix or env; investigate.
    _ = effort
    stdout = await _run_opencode_with_prompt_file(
        prompt,
        project_root,
        timeout,
        model,
        "Read the attached prompt file and respond to the council chat.",
        on_output=on_output,
        env=env,
    )
    cleaned = _strip_ansi(stdout)
    cleaned = _strip_opencode_header(cleaned)
    text = _sanitize_agent_output(cleaned, strip_thinking=True)
    text = _trim_opencode_progress_output(text)
    return DispatchResult(text=text, usage=_merge_usage(stdout, cleaned, text))


def _normalize_hermes_base_url(base_url: str) -> str:
    value = (base_url or "http://127.0.0.1:8642/v1").strip().rstrip("/")
    if not value:
        value = "http://127.0.0.1:8642/v1"
    if not value.endswith("/v1"):
        value = value + "/v1"
    return value


def _hermes_session_key(session_key: str, project_root: Path) -> str:
    key = (session_key or "").strip()
    if key:
        return key
    digest = hashlib.sha256(str(project_root.resolve()).encode("utf-8")).hexdigest()[:16]
    return f"council:{digest}"


def _build_hermes_chat_request(
    prompt: str,
    project_root: Path,
    model: str = "hermes-agent",
    base_url: str = "http://127.0.0.1:8642/v1",
    api_key: str = "",
    session_key: str = "",
) -> tuple[str, dict, dict]:
    url = urljoin(_normalize_hermes_base_url(base_url) + "/", "chat/completions")
    headers = {
        "Content-Type": "application/json",
        "X-Hermes-Session-Key": _hermes_session_key(session_key, project_root),
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "model": model or "hermes-agent",
        "messages": [
            {
                "role": "user",
                "content": prompt,
            }
        ],
        "stream": False,
    }
    return url, headers, payload


def _post_hermes_chat(url: str, headers: dict, payload: dict, timeout: int) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    request_timeout = None if timeout <= 0 else timeout
    try:
        with urllib.request.urlopen(req, timeout=request_timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        if exc.code == 403 and "X-Hermes-Session-Key requires API key authentication" in detail:
            raise RuntimeError(
                "Hermes API rejected the session key because API key authentication "
                "is not configured on both sides. Set API_SERVER_KEY in Hermes, set "
                "the same value in Council as COUNCIL_HERMES_API_KEY or "
                "api_keys.hermes, then restart Hermes."
            ) from exc
        raise RuntimeError(f"Hermes API returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        parsed = urlparse(url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if isinstance(exc.reason, ConnectionRefusedError):
            raise RuntimeError(
                f"Hermes API server is not listening on {host}:{port}. "
                "Set API_SERVER_ENABLED=true in Hermes and restart with "
                "`hermes gateway run --replace`."
            ) from exc
        raise RuntimeError(f"Hermes API request failed: {exc.reason}") from exc
    return json.loads(body)


def _extract_hermes_response(data: dict) -> DispatchResult:
    choices = data.get("choices")
    text = ""
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") if isinstance(choices[0], dict) else {}
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                parts = []
                for part in content:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        parts.append(part["text"])
                text = "\n".join(parts)
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    return DispatchResult(text=_sanitize_agent_output(text), usage=_merge_usage(json.dumps(usage)))


async def dispatch_hermes(
    prompt: str,
    project_root: Path,
    timeout: int = 300,
    model: str = "hermes-agent",
    base_url: str = "http://127.0.0.1:8642/v1",
    api_key: str = "",
    session_key: str = "",
    on_output: Callable[[str, str], Awaitable[None]] | None = None,
) -> DispatchResult:
    url, headers, payload = _build_hermes_chat_request(
        prompt,
        project_root,
        model=model,
        base_url=base_url,
        api_key=api_key,
        session_key=session_key,
    )
    if on_output:
        safe_headers = dict(headers)
        if "Authorization" in safe_headers:
            safe_headers["Authorization"] = "Bearer ***"
        await on_output(
            "http request",
            f"POST {url} headers={safe_headers} prompt_chars={len(prompt)}",
        )
    data = await asyncio.to_thread(_post_hermes_chat, url, headers, payload, timeout)
    result = _extract_hermes_response(data)
    if on_output:
        await on_output("http response", f"response_chars={len(result.text)}")
    return result
