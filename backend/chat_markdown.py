"""Helpers for reading Council chat markdown."""

from __future__ import annotations

import re
from collections.abc import Iterator

TURN_HEADER_LINE_RE = re.compile(r"^##\s+\[@([\w-]+)\]\s+(.+)$")
FENCE_LINE_RE = re.compile(r"^\s*(```+|~~~+)")
COMPACTED_STAMP_RE = re.compile(r"^compacted\s+(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})(\d{2})")
TURN_STAMP_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2}):(\d{2})")
PENDING_MARKER_RE = re.compile(r"^(?:No pending mentions\.|Pending mention:\s+@\w+)\s*$")


def iter_lines_with_offsets(text: str) -> Iterator[tuple[int, str]]:
    """Yield line start offsets with lines stripped of trailing newline chars."""
    offset = 0
    for raw_line in text.splitlines(keepends=True):
        line = raw_line.rstrip("\r\n")
        yield offset, line
        offset += len(raw_line)


def _sortable_compaction_stamp(text: str) -> str:
    match = COMPACTED_STAMP_RE.match(text)
    if not match:
        return ""
    return f"{match.group(1)}-{match.group(2)}-{match.group(3)} {match.group(4)}:{match.group(5)}:{match.group(6)}"


def _sortable_turn_stamp(text: str) -> str:
    match = TURN_STAMP_RE.match(text)
    if not match:
        return ""
    return f"{match.group(1)}-{match.group(2)}-{match.group(3)} {match.group(4)}:{match.group(5)}:{match.group(6)}"


def _single_line_fence(line: str, marker_text: str) -> bool:
    """Return True for inline-ish ``` text ``` lines that should not hide turns."""
    rest = line[line.find(marker_text) + len(marker_text):]
    return marker_text in rest


def iter_turn_header_matches(text: str) -> Iterator[tuple[int, re.Match[str]]]:
    """Yield live Council turn headers, ignoring headers embedded in summaries."""
    fence_marker = ""
    fence_len = 0
    in_compaction_body = False
    compaction_pending_marker_seen = False
    skipping_duplicated_chat = False
    compaction_cutoff = ""

    for offset, line in iter_lines_with_offsets(text):
        fence = FENCE_LINE_RE.match(line)
        if fence:
            marker_text = fence.group(1)
            if _single_line_fence(line, marker_text):
                pass
            else:
                marker = marker_text[0]
                marker_len = len(marker_text)
                if fence_marker == marker and marker_len >= fence_len:
                    fence_marker = ""
                    fence_len = 0
                elif not fence_marker:
                    fence_marker = marker
                    fence_len = marker_len
                continue
        if fence_marker:
            continue
        match = TURN_HEADER_LINE_RE.match(line)
        if match:
            author = match.group(1)
            stamp = match.group(2)
            if skipping_duplicated_chat:
                turn_stamp = _sortable_turn_stamp(stamp)
                if turn_stamp and compaction_cutoff and turn_stamp <= compaction_cutoff:
                    continue
                skipping_duplicated_chat = False
                in_compaction_body = False
                compaction_pending_marker_seen = False

            if in_compaction_body:
                if compaction_pending_marker_seen:
                    if author == "system" and stamp.startswith("compacted "):
                        skipping_duplicated_chat = True
                        continue
                    in_compaction_body = False
                    compaction_pending_marker_seen = False
                else:
                    if author == "system" and stamp.startswith("compacted "):
                        continue
                    in_compaction_body = False

            yield offset, match
            if author == "system" and stamp.startswith("compacted "):
                in_compaction_body = True
                compaction_pending_marker_seen = False
                skipping_duplicated_chat = False
                compaction_cutoff = _sortable_compaction_stamp(stamp)
            continue

        if in_compaction_body and PENDING_MARKER_RE.match(line):
            compaction_pending_marker_seen = True
