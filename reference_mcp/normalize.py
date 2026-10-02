"""Transcript parsers — normalize each tool's JSONL into a common `Message`.

Every coding agent stores sessions a little differently. We read each tool's
on-disk transcript format and emit a uniform `Message` record so the rest of the
codebase never has to care which tool a line came from.

Adding a new tool usually means adding one parser here (see ``parse_generic``)
and one entry in ``adapters.py``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

# Tool output (results of running commands, file dumps) is truncated, not dropped:
# we keep enough to stay searchable ("which tool fixed this?") without bloating
# the index with multi-kilobyte file contents.
TOOL_RESULT_MAX = 600
TOOL_INPUT_MAX = 160


@dataclass
class Message:
    """One normalized turn from any tool's transcript."""

    source: str  # tool name: "claude", "codex", ...
    role: str  # user | assistant | system | summary
    text: str
    ts: datetime | None
    session_id: str
    project: str  # cwd / project path if the transcript records one
    path: str  # transcript file the line came from
    uuid: str = ""


def _iter_lines(path: str) -> Iterator[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield line
    except OSError:
        return


def parse_ts(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def blocks_to_text(content: Any, keep_thinking: bool = True) -> str:
    """Flatten a message ``content`` (string or list of blocks) into plain text.

    Handles the block shapes seen across Claude Code and Codex: ``text``,
    ``input_text``/``output_text``, ``thinking``, ``tool_use``, ``tool_result``.
    """
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
            continue
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype in ("text", "input_text", "output_text"):
            parts.append(block.get("text", "") or "")
        elif btype == "thinking":
            if keep_thinking:
                parts.append("[thinking] " + (block.get("thinking") or block.get("text", "") or ""))
        elif btype == "tool_use":
            name = block.get("name", "tool")
            args = json.dumps(block.get("input", {}), ensure_ascii=False)[:TOOL_INPUT_MAX]
            parts.append(f"[tool:{name}] {args}")
        elif btype == "tool_result":
            inner = block.get("content")
            if isinstance(inner, list):
                inner = " ".join(x.get("text", "") for x in inner if isinstance(x, dict))
            tag = "[error]" if block.get("is_error") else "[result]"
            parts.append(f"{tag} {str(inner or '')[:TOOL_RESULT_MAX]}")
    return "\n".join(p for p in parts if p).strip()


def parse_claude(path: str, keep_thinking: bool = True) -> Iterator[Message]:
    """Parse a Claude Code session JSONL (``~/.claude/projects/<enc>/<uuid>.jsonl``)."""
    fallback_sid = Path(path).stem
    for line in _iter_lines(path):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        ltype = obj.get("type")
        if ltype not in ("user", "assistant", "summary"):
            continue
        if ltype == "summary":
            text, role = (obj.get("summary", "") or ""), "summary"
        else:
            msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
            role = msg.get("role") or ltype
            text = blocks_to_text(msg.get("content"), keep_thinking)
        if not text:
            continue
        yield Message(
            source="claude",
            role=role,
            text=text,
            ts=parse_ts(obj.get("timestamp")),
            session_id=obj.get("sessionId") or fallback_sid,
            project=obj.get("cwd") or "",
            path=path,
            uuid=obj.get("uuid", ""),
        )


def _codex_session_id(path: str) -> str:
    stem = Path(path).stem  # rollout-<ts>-<uuid>
    parts = stem.split("-")
    return "-".join(parts[-5:]) if len(parts) >= 5 else stem


def parse_codex(path: str, keep_thinking: bool = True) -> Iterator[Message]:
    """Parse a Codex CLI rollout JSONL (``~/.codex/sessions/.../rollout-*.jsonl``)."""
    sid = _codex_session_id(path)
    for line in _iter_lines(path):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
        ptype = payload.get("type")
        if ptype == "user_message":
            role = "user"
        elif ptype in ("message", "agent_message", "transcript_segment"):  # transcript_segment = voice mode
            role = payload.get("role", "assistant") or "assistant"
        else:
            continue
        body = payload.get("content")
        if body is None:
            body = payload.get("text") or payload.get("message")
        text = blocks_to_text(body, keep_thinking)
        if not text:
            continue
        yield Message(
            source="codex",
            role=role,
            text=text,
            ts=parse_ts(obj.get("timestamp")),
            session_id=sid,
            project=payload.get("cwd", "") or "",
            path=path,
            uuid="",
        )


def parse_generic(path: str, keep_thinking: bool = True) -> Iterator[Message]:
    """Best-effort parser for unknown JSONL transcripts.

    Tries the Claude shape first (``type``/``message``), then the Codex shape
    (``payload``). Good enough to make a new tool searchable before a dedicated
    parser exists.
    """
    sid = Path(path).stem
    for line in _iter_lines(path):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        role = obj.get("role") or (obj.get("message") or {}).get("role") if isinstance(obj.get("message"), dict) else obj.get("role")
        content = obj.get("content")
        if content is None and isinstance(obj.get("message"), dict):
            content = obj["message"].get("content")
        if content is None and isinstance(obj.get("payload"), dict):
            content = obj["payload"].get("content") or obj["payload"].get("text")
            role = role or {"user_message": "user"}.get(obj["payload"].get("type"), "assistant")
        text = blocks_to_text(content, keep_thinking)
        if not text:
            continue
        yield Message(
            source=Path(path).parent.name or "generic",
            role=role or "user",
            text=text,
            ts=parse_ts(obj.get("timestamp") or obj.get("ts")),
            session_id=sid,
            project=obj.get("cwd", "") or "",
            path=path,
        )


PARSERS: dict[str, Callable[..., Iterator[Message]]] = {
    "claude": parse_claude,
    "codex": parse_codex,
    "generic": parse_generic,
}
