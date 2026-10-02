"""Tests for parsing + cross-tool search. Run: `uv run --with pytest pytest -q`."""

from pathlib import Path

from reference_mcp import normalize
from reference_mcp.search import Hit, Index, MemoryHit, tokens
from reference_mcp.server import _memory_evidence, _message_evidence

FIX = Path(__file__).parent / "fixtures"


def test_parse_claude():
    msgs = list(normalize.parse_claude(str(FIX / "claude_sample.jsonl")))
    roles = [m.role for m in msgs]
    assert roles == ["user", "assistant", "assistant"]
    assert msgs[0].source == "claude"
    assert "pg necessity" in msgs[0].text.lower()
    # thinking + text both captured on the assistant turn
    assert "[thinking]" in msgs[1].text and "vertical prior" in msgs[1].text
    # tool_use truncated/tagged, not dropped
    assert "[tool:Bash]" in msgs[2].text


def test_parse_codex():
    msgs = list(normalize.parse_codex(str(FIX / "codex_sample.jsonl")))
    assert [m.role for m in msgs] == ["user", "assistant"]
    assert msgs[0].source == "codex"
    assert "gemini grounding" in msgs[0].text.lower()
    # task_started and other non-message events are skipped
    assert all(m.role in ("user", "assistant") for m in msgs)


def test_parse_codex_voice():
    # Codex voice mode (ChatGPT desktop app) logs speech as realtime_item / transcript_segment
    msgs = list(normalize.parse_codex(str(FIX / "codex_voice_sample.jsonl")))
    assert [m.role for m in msgs] == ["user", "assistant"]
    assert "codeword be pvc" in msgs[0].text.lower()
    # session start/close events are skipped
    assert all("realtime_session" not in m.text for m in msgs)
    hits = Index(msgs).search("codeword pvc")
    assert hits and hits[0].message.source == "codex"


def test_cross_tool_search():
    msgs = list(normalize.parse_claude(str(FIX / "claude_sample.jsonl")))
    msgs += list(normalize.parse_codex(str(FIX / "codex_sample.jsonl")))
    idx = Index(msgs)

    rzp = idx.search("razorpay pg necessity gate")
    assert rzp and rzp[0].message.source == "claude"

    gem = idx.search("gemini grounding signals")
    assert gem and gem[0].message.source == "codex"

    # source filter works
    only_codex = idx.search("gemini", source="codex")
    assert only_codex and all(h.message.source == "codex" for h in only_codex)
    assert idx.search("gemini", source="claude") == []


def test_tokens_drop_stopwords():
    assert "the" not in tokens("the gemini grounding")
    assert "gemini" in tokens("the gemini grounding")


def test_message_evidence_has_verification_path():
    msgs = list(normalize.parse_claude(str(FIX / "claude_sample.jsonl")))
    hit = Hit(msgs[0], 1.23456)

    ev = _message_evidence(hit, "pg necessity")

    assert ev["kind"] == "session_turn"
    assert ev["source"] == "claude"
    assert ev["role"] == "user"
    assert ev["session_id"] == msgs[0].session_id
    assert ev["path"] == msgs[0].path
    assert ev["score"] == 1.2346
    assert ev["verification_path"]["tool"] == "get_session"


def test_memory_evidence_has_source_path():
    hit = MemoryHit(source="claude", path="/tmp/CLAUDE.md", score=3, snippet="Use Postgres, not SQLite")

    ev = _memory_evidence(hit)

    assert ev == {
        "kind": "memory_file",
        "score": 3,
        "source": "claude",
        "path": "/tmp/CLAUDE.md",
        "snippet": "Use Postgres, not SQLite",
        "verification_path": {"tool": "search_memory", "source_path": "/tmp/CLAUDE.md"},
    }
