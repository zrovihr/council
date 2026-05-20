"""Tool provenance helpers for linking trace activity to agent turns."""

from __future__ import annotations

import re

from .state import clean_trace_text

MAX_DETAIL_CHARS = 220
MAX_ITEMS = 18

PATCH_FILE_RE = re.compile(r"^\*\*\* (Add|Update|Delete) File:\s+(.+)$")
PATH_RE = re.compile(
    r"(?P<path>(?:[A-Za-z]:[\\/]|/|\.\.?[\\/])?[\w.!$%&()+,;=@^`{}~ -]+[\\/][\w.!$%&()+,;=@^`{}~ -]+\.[A-Za-z0-9]{1,12})"
)
SHELL_COMMAND_RE = re.compile(r'"command"\s*:\s*"((?:\\.|[^"\\])*)"')
SHELL_READ_RE = re.compile(
    r"\b(Get-Content|rg|Select-String|Get-ChildItem|git\s+(?:status|diff|show|log))\b",
    re.IGNORECASE,
)
SHELL_TEST_RE = re.compile(
    r"\b(pytest|unittest|node\s+--check|npm\s+test|python\s+-m\s+pytest|python\s+-m\s+unittest)\b",
    re.IGNORECASE,
)
SHELL_MUTATION_RE = re.compile(
    r"\b(apply_patch|Set-Content|Out-File|New-Item|Move-Item|Copy-Item)\b",
    re.IGNORECASE,
)
SHELL_DELETE_RE = re.compile(r"\b(Remove-Item|del|erase|rmdir)\b", re.IGNORECASE)
CLI_STATUS_RE = re.compile(
    r"^\s*(?P<marker>(?:->|<-|[\u2192\u2190]))?\s*(?P<action>Read|Edit|Write|Update|Create|Delete|Search|Grep)\s+"
    r"(?P<target>.+?)\s*$",
    re.IGNORECASE,
)
CLI_COMMAND_RE = re.compile(r"^\s*\$\s+(?P<command>.+?)\s*$")


def build_tool_provenance(trace_events: list[dict], max_items: int = MAX_ITEMS) -> list[dict]:
    """Return compact, user-visible tool activity extracted from trace chunks."""
    items: list[dict] = []
    seen: set[tuple[str, str, str]] = set()

    def add(kind: str, label: str, detail: str = "", paths: list[str] | None = None) -> None:
        clean_paths = _dedupe([_normalize_path(p) for p in (paths or []) if p])
        clean_detail = _trim(clean_trace_text(detail), MAX_DETAIL_CHARS)
        key = (kind, label, "|".join(clean_paths) or clean_detail)
        if key in seen:
            return
        seen.add(key)
        item = {
            "kind": kind,
            "label": _trim(label, 90),
        }
        if clean_detail and clean_detail != item["label"]:
            item["detail"] = clean_detail
        if clean_paths:
            item["paths"] = clean_paths[:5]
        items.append(item)

    for event in trace_events:
        message = clean_trace_text(event.get("message") or "").strip().lower()
        detail = clean_trace_text(event.get("detail") or "")
        if not detail:
            continue
        if "stdout" not in message and "stderr" not in message and "tool" not in message:
            continue
        _extract_patch_activity(detail, add)
        _extract_cli_status_activity(detail, add)
        _extract_shell_activity(detail, add)

    return items[:max_items]


def _extract_patch_activity(detail: str, add) -> None:
    for line in detail.splitlines():
        match = PATCH_FILE_RE.match(line.strip())
        if not match:
            continue
        action, path = match.groups()
        kind = {
            "Add": "write",
            "Update": "write",
            "Delete": "delete",
        }[action]
        verb = {
            "Add": "added",
            "Update": "edited",
            "Delete": "deleted",
        }[action]
        add(kind, f"{verb} {_display_path(path)}", paths=[path])


def _extract_shell_activity(detail: str, add) -> None:
    for command in _shell_commands(detail):
        _add_shell_command(command, add)


def _extract_cli_status_activity(detail: str, add) -> None:
    for line in detail.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        command_match = CLI_COMMAND_RE.match(stripped)
        if command_match:
            _add_shell_command(command_match.group("command"), add)
            continue

        match = CLI_STATUS_RE.match(stripped)
        if not match:
            continue
        action = match.group("action").lower()
        target = _status_target_path(match.group("target"))
        if not target:
            continue

        if action in {"edit", "write", "update", "create"}:
            add("write", f"edited {_display_path(target)}", stripped, [target])
        elif action == "delete":
            add("delete", f"deleted {_display_path(target)}", stripped, [target])
        elif action in {"search", "grep"}:
            add("search", f"searched {_display_path(target)}", stripped, [target])
        else:
            add("read", f"read {_display_path(target)}", stripped, [target])


def _add_shell_command(command: str, add) -> None:
    paths = _paths_from_text(command)
    label_path = _display_path(paths[0]) if paths else _trim(command, 70)
    if SHELL_DELETE_RE.search(command):
        add("delete", f"delete command: {label_path}", command, paths)
    elif SHELL_MUTATION_RE.search(command):
        add("write", f"write command: {label_path}", command, paths)
    elif SHELL_TEST_RE.search(command):
        add("test", "ran verification", command, paths)
    elif SHELL_READ_RE.search(command):
        kind = "search" if re.search(r"\b(rg|Select-String)\b", command, re.IGNORECASE) else "read"
        verb = "searched" if kind == "search" else "read"
        add(kind, f"{verb} {label_path}", command, paths)
    else:
        add("command", f"ran {label_path}", command, paths)


def _shell_commands(detail: str) -> list[str]:
    commands = []
    for match in SHELL_COMMAND_RE.finditer(detail):
        command = _decode_json_string(match.group(1)).strip()
        if command:
            commands.append(command)
    if commands:
        return commands

    lines = [line.strip() for line in detail.splitlines() if line.strip()]
    likely = []
    for line in lines:
        command_match = CLI_COMMAND_RE.match(line)
        command = command_match.group("command") if command_match else line
        if command.startswith(("Get-Content ", "rg ", "git ", "node ", "pytest ", "python ", ".\\")):
            likely.append(command)
    return likely


def _decode_json_string(text: str) -> str:
    try:
        return bytes(text, "utf-8").decode("unicode_escape")
    except UnicodeDecodeError:
        return text.replace(r"\"", '"').replace(r"\\", "\\")


def _paths_from_text(text: str) -> list[str]:
    paths = [match.group("path").strip("`'\".,);]") for match in PATH_RE.finditer(text)]
    for token in re.findall(r"(?<!\S)(?:\.?[\w-]+\.)+[\w-]+(?!\S)", text):
        paths.append(_strip_path_token(token))
    return _dedupe(paths)


def _status_target_path(target: str) -> str:
    target = re.sub(r"\s+\[[^\]]+\]\s*$", "", target.strip())
    target = _strip_path_token(target)
    if not target:
        return ""
    paths = _paths_from_text(target)
    if paths:
        return paths[0]
    first = _strip_path_token(target.split()[0]) if target.split() else ""
    if first.startswith(".") or "/" in first or "\\" in first:
        return first
    if re.match(r"^[\w-]+\.[A-Za-z0-9][\w.-]*$", first):
        return first
    return ""


def _strip_path_token(token: str) -> str:
    return token.strip("`'\"").rstrip(".,);]")


def _normalize_path(path: str) -> str:
    return path.replace("\\", "/").strip() if path else path


def _display_path(path: str) -> str:
    path = _normalize_path(path)
    parts = path.replace("\\", "/").split("/")
    return "/".join(parts[-3:]) if len(parts) > 3 else path


def _dedupe(values) -> list[str]:
    result = []
    seen = set()
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _trim(text: str, max_chars: int) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 1)].rstrip() + "..."
