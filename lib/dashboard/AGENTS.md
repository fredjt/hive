# AGENTS.md

This file documents the `this directory` area of the Hive project — what it does, how it works, and the conventions for extending it.

## What is Hive Dashboard

A self-contained Node.js web UI for viewing and managing the Hive company orchestration framework. Serves as the "Company Board" — ticket boards, chat, meetings, sprint state, agent activity, git diffs, and more.

**Run:** `node server.js --dir .company --port 3456` (or `npm start` from this directory)

**Zero dependencies** — uses only Node.js built-in modules (`http`, `fs`, `path`, `child_process`). No build step, no framework.

## Architecture

### Single-file server (`server.js`, ~5800 lines)

Everything lives in one file. Key sections:

| Lines | Section |
|-------|---------|
| 12–108 | MCP client — all write operations proxy to the Hive MCP server at `http://127.0.0.1:7891/mcp` (configurable via `HIVE_MCP_URL` env var). Session management via `mcp-session-id` header. |
| 110–246 | SSE event engine — file watchers on `.company/` subdirectories emit debounced SSE events (`ticket-change`, `chat-change`, `log-change`, `spawn-stream-change`, etc.) with fingerprint dedup. |
| 248–374 | Request parsing & routing helpers |
| 376–2089 | API endpoint handlers (~35 endpoints). All reads are file-based; writes go through the MCP client. |
| 2091–5773 | Embedded frontend — GitHub-dark themed SPA with vanilla JS, ~17 view panels, no external libraries. |
| 5775–5795 | HTTP server bootstrap |

### Data flow

The dashboard is **read-only from the `.company/` directory**. All mutations proxy through the MCP server (`lib/hive_mcp/server.py`), which shims to bash scripts in `lib/tools/`.

### Frontend panels

Vanilla JS SPA with client-side routing. Panels: Dashboard, Board (Kanban), Chat, Meetings, Sprint, Org Chart, Agents, Reports, Logs, Verification, Activity, Rounds, GitHub, Releases, Config (C2 — resolved LLM + last doctor dump + last llm test), Git, Inbox/Outbox, Brief, Worktree Browser. Each panel fetches from `/api/` endpoints and renders from templates.

### SSE live updates

`GET /api/events` opens an SSE connection. File watchers on `.company/` subdirectories fire events with debounce windows (500ms default, 50ms for spawn/cycle/log streams). Fingerprint dedup skips re-rendering byte-identical data to preserve scroll position.

## Key conventions

- **No external dependencies** — adding one requires updating `package.json` and justifying the need.
- **No build step** — the file is edited and served directly.
- **The SPA is a template literal: escape backslashes twice.** The embedded
  HTML/JS is built by evaluating a JS template literal, so every `\n` you
  type in the script section arrives at the browser as a RAW NEWLINE —
  inside a string literal that is a SyntaxError that kills the entire SPA
  (the server itself parses fine, so `node --check server.js` cannot see
  it; `tests/test_dashboard.py::TestEmbeddedSpaScript` syntax-checks the
  DELIVERED script and catches it). When the browser should see a newline
  escape, write `\\n` (two backslashes + n in the file, one backslash + n
  in the browser). The existing `\\'` spellings in onclick builders are the
  same convention: two backslashes + quote in the file, one + quote in the
  browser.
- **All API responses are JSON** (GET/POST) except `/api/events` (SSE) and the root route (HTML).
- **Ticket statuses/types/priorities**: see root `AGENTS.md` for the full list.
- **MCP write methods:** `ticket_create`, `ticket_move`, `ticket_comment`, `ticket_approve`, `inbox_write`, `gates_run`, `gates_check`

## Common operations

### Run the dashboard
```bash
node server.js --dir .company --port 3456
```

### Add an API endpoint
1. Add a handler in the `handleApi()` dispatch block (~line 376–2089)
2. Add a corresponding frontend panel or update an existing one in the embedded HTML section (~line 2091+)

### Add an SSE event type
1. Extend the file watcher in the SSE engine section (~line 110–246)
2. Add a client-side event listener in the SPA's `connectEvents()` function

### Modify the frontend
- CSS is in a `<style>` block near line 2091
- JS SPA logic is mixed in `<script>` tags throughout the embedded HTML
- Templates are string interpolation in JS functions

## See also

- Parent `AGENTS.md` — Framework-level architecture, common commands
- `lib/AGENTS.md` — Cross-module data flow, module map
