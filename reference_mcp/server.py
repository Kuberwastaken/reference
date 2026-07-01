"""The Reference MCP server.

Exposes cross-tool recall as MCP tools so any agent (Claude Code, Codex, Cursor,
...) can search every past session and memory file from every configured tool.
Tools return human-readable text because the consumer is an LLM agent.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from mcp.server.fastmcp import FastMCP

from . import __version__, search
from .adapters import iter_files, load_adapters


def _since(since_days: int | None) -> datetime | None:
    if not since_days or since_days <= 0:
        return None
    return datetime.now(timezone.utc) - timedelta(days=since_days)


def _fmt_ts(ts: datetime | None) -> str:
    return ts.strftime("%Y-%m-%d %H:%M") if ts else "unknown-time"


def _iso_ts(ts: datetime | None) -> str | None:
    return ts.isoformat() if ts else None


def _message_evidence(hit: search.Hit, query: str) -> dict:
    m = hit.message
    return {
        "kind": "session_turn",
        "score": round(hit.score, 4),
        "source": m.source,
        "role": m.role,
        "timestamp": _iso_ts(m.ts),
        "project": m.project or None,
        "session_id": m.session_id,
        "path": m.path,
        "uuid": m.uuid or None,
        "snippet": search.snippet(m.text, query),
        "verification_path": {
            "tool": "get_session",
            "session_ref": m.session_id or m.path,
            "source_path": m.path,
        },
    }


def _memory_evidence(hit: search.MemoryHit) -> dict:
    return {
        "kind": "memory_file",
        "score": hit.score,
        "source": hit.source,
        "path": hit.path,
        "snippet": hit.snippet,
        "verification_path": {"tool": "search_memory", "source_path": hit.path},
    }


def build_server() -> FastMCP:
    mcp = FastMCP("reference")

    @mcp.tool()
    def search_sessions(
        query: str,
        source: str | None = None,
        project: str | None = None,
        role: str | None = None,
        since_days: int | None = None,
        limit: int = 10,
    ) -> str:
        """Search past session transcripts across ALL configured tools.

        Args:
            query: words/phrase to look for.
            source: restrict to one tool ("claude", "codex", ...). Omit for all.
            project: substring of the project path (cwd) to restrict to.
            role: restrict to "user" or "assistant" turns.
            since_days: only turns newer than N days.
            limit: max results (default 10).
        """
        hits = search.get_index().search(
            query, source=source, project=project, role=role, since=_since(since_days), limit=limit
        )
        if not hits:
            return f"No matches for {query!r}."
        lines = [f"{len(hits)} match(es) for {query!r}:\n"]
        for i, h in enumerate(hits, 1):
            m = h.message
            proj = m.project or "?"
            lines.append(
                f"{i}. [{m.source}/{m.role}] {_fmt_ts(m.ts)} · {proj}\n"
                f"   session: {m.session_id}\n"
                f"   {search.snippet(m.text, query)}\n"
            )
        return "\n".join(lines)

    @mcp.tool()
    def search_memory(query: str, source: str | None = None, limit: int = 10) -> str:
        """Search memory / instruction files (CLAUDE.md, AGENTS.md, memory/*.md) across all tools."""
        hits = search.search_memory(query, source=source, limit=limit)
        if not hits:
            return f"No memory matches for {query!r}."
        out = [f"{len(hits)} memory match(es) for {query!r}:\n"]
        for i, h in enumerate(hits, 1):
            out.append(f"{i}. [{h.source}] {h.path}\n   {h.snippet}\n")
        return "\n".join(out)

    @mcp.tool()
    def recall(query: str, limit: int = 8) -> str:
        """Combined recall: best matching past session turns AND memory/instruction
        snippets across every tool. Use this when you just want relevant context."""
        sess = search.get_index().search(query, limit=limit)
        mem = search.search_memory(query, limit=max(3, limit // 2))
        out: list[str] = []
        if mem:
            out.append("## Memory / instructions")
            for h in mem:
                out.append(f"- [{h.source}] {h.path}\n  {h.snippet}")
        if sess:
            out.append("\n## Past sessions")
            for h in sess:
                m = h.message
                out.append(
                    f"- [{m.source}/{m.role}] {_fmt_ts(m.ts)} · {m.project or '?'} (session {m.session_id})\n"
                    f"  {search.snippet(m.text, query)}"
                )
        return "\n".join(out) if out else f"Nothing found for {query!r}."

    @mcp.tool()
    def recall_with_sources(query: str, limit: int = 8) -> str:
        """Combined recall with machine-readable evidence metadata.

        Use this before relying on an old-session or memory hit as current truth.
        The response keeps the same search coverage as recall(), but returns JSON
        with source type, timestamp/freshness, session/file refs, snippets, and a
        verification path so agents can inspect the source before acting.
        """
        sess = search.get_index().search(query, limit=limit)
        mem = search.search_memory(query, limit=max(3, limit // 2))
        payload = {
            "query": query,
            "result_count": len(mem) + len(sess),
            "memory": [_memory_evidence(h) for h in mem],
            "sessions": [_message_evidence(h, query) for h in sess],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    @mcp.tool()
    def list_sessions(source: str | None = None, project: str | None = None, limit: int = 20) -> str:
        """List recent sessions (most recent first) with tool, project, time and turn count."""
        rows = search.list_sessions(source=source, project=project, limit=limit)
        if not rows:
            return "No sessions found."
        out = [f"{len(rows)} recent session(s):\n"]
        for r in rows:
            out.append(
                f"- [{r['source']}] {_fmt_ts(r['last_ts'])} · {r['project'] or '?'}\n"
                f"  session: {r['session_id']} · {r['count']} turns"
            )
        return "\n".join(out)

    @mcp.tool()
    def get_session(session_ref: str, max_chars: int = 16000) -> str:
        """Return the full cleaned transcript of one session, by session id or file path."""
        msgs = search.get_session_messages(session_ref)
        if not msgs:
            return f"No session matched {session_ref!r}. Use list_sessions to find one."
        head = msgs[0]
        out = [f"Session {head.session_id} [{head.source}] · {head.project or '?'} · {len(msgs)} turns\n"]
        budget = max_chars
        for m in msgs:
            block = f"--- {m.role} ({_fmt_ts(m.ts)}) ---\n{m.text}\n"
            if budget - len(block) < 0:
                out.append(f"… (truncated; raise max_chars to see more)")
                break
            out.append(block)
            budget -= len(block)
        return "\n".join(out)

    @mcp.tool()
    def list_sources() -> str:
        """Show which tools/adapters are configured and how many session & memory files
        each currently resolves to. Useful to confirm Reference can see a tool's data."""
        out = ["Configured tools (adapters):\n"]
        for a in load_adapters():
            n_sess = len(iter_files(a.session_globs))
            n_mem = len(iter_files(a.memory_globs))
            out.append(
                f"- {a.name}  [format={a.session_format}]\n"
                f"    sessions: {n_sess} file(s)  ·  memory: {n_mem} file(s)"
            )
        out.append(f"\nReference v{__version__}")
        return "\n".join(out)

    return mcp


def serve() -> None:
    build_server().run()
