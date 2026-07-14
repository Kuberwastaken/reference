---
name: reference
description: >-
  Install, set up, and use Reference — one MCP server that lets an AI agent
  search the past session transcripts AND memory files (CLAUDE.md, AGENTS.md,
  memory/*.md) of every AI coding tool on the machine (Claude Code, Codex CLI,
  Cursor, VS Code, …). Use this skill when the user wants cross-tool recall
  ("what did we decide in Codex?", "find the session where we wired X", "search
  my past chats"), when setting Reference up in a new host, or when an agent
  should remember work done in a different tool. Local-first, read-only, offline
  BM25 — nothing leaves the machine.
---

# Reference — cross-tool session & memory recall for agents

**Reference is one [MCP](https://modelcontextprotocol.io) server you register in each AI
coding tool. It reads every tool's session transcripts _and_ memory files from the local
machine, so any agent can recall what any of them did before.** Claude Code forgets what you
did in Codex; Codex can't see your Claude history; Cursor knows neither. Reference fixes that.

It runs straight from GitHub with [`uv`](https://docs.astral.sh/uv/) — no PyPI, no clone, no
build step. Pure-Python, offline BM25 ranking, only dependency is `mcp`. Read-only: it never
writes to your transcripts and never phones home.

Repo: <https://github.com/Kuberwastaken/reference> · License: MIT

---

## When to use this skill

- The user asks to **recall / search past sessions or chats** across tools
  ("what did we decide about X", "find where we set up Y", "search my Codex history").
- The user wants an agent to **remember work done in a different tool** (Claude ⇄ Codex ⇄ Cursor).
- The user wants to **install or set Reference up** in the current host, or add support for a new tool.

If Reference is already registered in this host, you'll have tools named `recall`,
`search_sessions`, `search_memory`, `list_sessions`, `get_session`, `list_sources` — just use
them (see [Using it](#using-it-mcp-tools)). If you don't, run the setup below first.

---

## Setup (install + register in the current host)

The command that runs the server is **always** this — there is nothing to install first,
`uvx` fetches and caches it on first run:

```
uvx --from git+https://github.com/Kuberwastaken/reference reference-mcp
```

### 1. Verify it can see your data

```bash
uvx --from git+https://github.com/Kuberwastaken/reference reference-mcp doctor
```

`doctor` lists each tool it knows about and how many session/memory files each resolves. If
your tools show non-zero counts, you're ready to register. If a tool you use shows 0 (or is
missing), add an adapter — see [Adding a tool](#adding-support-for-a-new-tool).

### 2. Register the server in this host

Pick the block for the tool you're running in. It's always a **stdio** server named `reference`.

**Claude Code**
```bash
claude mcp add reference -- uvx --from git+https://github.com/Kuberwastaken/reference reference-mcp
```

**Codex CLI** — add to `~/.codex/config.toml`:
```toml
[mcp_servers.reference]
command = "uvx"
args = ["--from", "git+https://github.com/Kuberwastaken/reference", "reference-mcp"]
```

**Cursor** — add to `~/.cursor/mcp.json` (or `.cursor/mcp.json` in a project):
```json
{ "mcpServers": { "reference": { "command": "uvx",
  "args": ["--from", "git+https://github.com/Kuberwastaken/reference", "reference-mcp"] } } }
```

**VS Code (MCP)** — add to `.vscode/mcp.json`:
```json
{ "servers": { "reference": { "command": "uvx",
  "args": ["--from", "git+https://github.com/Kuberwastaken/reference", "reference-mcp"] } } }
```

Any other MCP host: run the `uvx …reference-mcp` command as a **stdio** server named `reference`.
`reference-mcp install <claude|codex|cursor|vscode>` prints the exact snippet for that host.

### 3. Confirm

Restart / reload the host so it picks up the new server, then call the `list_sources` tool
(or re-run `doctor`). You should see sessions from *other* tools too. Try
`recall("<something you did elsewhere>")`.

### 4. Make it durable — tell future agents about it

So that agents in *future* sessions reach for Reference instead of re-deriving lost context,
add a short pointer to the host's always-loaded memory file — **`CLAUDE.md` for Claude Code,
`AGENTS.md` for Codex/Cursor** (user-level `~/.claude/CLAUDE.md` / `~/.codex/AGENTS.md` for
every project, or the project's own file for one repo). Append this block if it isn't there:

```markdown
# Cross-tool recall (Reference MCP)
- The **reference** MCP is registered here. It searches past session transcripts AND memory
  files across every AI tool on this machine (Claude Code, Codex, Cursor, …).
- Before re-deriving lost context, RECALL it: use `recall("<topic>")` for a first pass, then
  `search_sessions` / `search_memory` to narrow, and `get_session` to read a full transcript.
- Reach for it whenever the user references a decision, setup, or discussion that happened in
  another tool or a past session ("what did we decide…", "find the session where…").
```

Keep the wording tight — this file is loaded into every session, so one short section is enough.

---

## Using it (MCP tools)

| Tool | What it does |
|---|---|
| `recall(query, limit?)` | **Start here.** Best-matching past-session turns **and** memory snippets across every tool. |
| `recall_evidence(query, limit?)` | Same coverage as `recall`, but returns JSON with source type, freshness, session/file refs, and a verification path. Use before trusting an old hit as current truth. |
| `search_sessions(query, source?, project?, role?, since_days?, limit?)` | Full-text search over all transcripts. Filter by tool, project path, role, or recency. |
| `search_memory(query, source?, limit?)` | Search `CLAUDE.md` / `AGENTS.md` / `memory/*.md` across tools. |
| `list_sessions(source?, project?, limit?)` | Recent sessions (newest first) with tool, project, time, turn count. |
| `get_session(session_ref, max_chars?)` | Full cleaned transcript of one session, by id or path. |
| `list_sources()` | Which tools are configured and how many files each resolves. |

`source` is a tool name (`claude`, `codex`, …); omit it to search everything. Typical flow:
`recall` to find candidates → `search_sessions`/`search_memory` to narrow → `get_session` to
read the full context.

---

## Adding support for a new tool

Built-ins (Claude Code, Codex) need no config. To teach Reference about another tool, drop a
`reference.toml` at `~/.config/reference-mcp/reference.toml` (or point `REFERENCE_MCP_CONFIG`
at one):

```toml
[[tool]]
name = "cursor"
session_globs = ["~/.cursor/chats/**/*.jsonl"]
session_format = "generic"   # claude | codex | generic
memory_globs = ["~/.cursor/rules/**/*.md"]
```

Adding a tool makes its history visible **in every other tool** at once. Verify with
`reference-mcp doctor` (non-zero session count) and a `recall(...)` query. If you wired up a
tool that isn't built in, please contribute the adapter back — the repo's `AGENTS.md` walks an
agent through promoting it to a built-in and opening a PR.

---

## Privacy & security

Reference is **local-first and read-only**. It reads transcript/memory files already on disk
and serves results over local stdio to your agent. It does not upload, phone home, or write to
your transcripts. Sessions can contain secrets and source code — only register Reference in
agents you trust, and never commit a `.reference-cache/` or raw transcripts.
