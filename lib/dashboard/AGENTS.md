# AGENTS.md

This file documents the `this directory` area of the Hive project — what it does, how it works, and the conventions for extending it.

## What is Hive Dashboard

A self-contained Node.js web UI for viewing and managing the Hive company orchestration framework. Serves as the "Company Board" — ticket boards, chat, meetings, sprint state, agent activity, git diffs, and more.

**Run:** `node server.js --dir .company --port 3456` (or `npm start` from this directory)

**Zero dependencies** — uses only Node.js built-in modules (`http`, `fs`, `path`, `child_process`). No build step, no framework.

## Architecture

### Module layout (G8: the 5,677-line monolith is split)

| File | Section |
|------|---------|
| `server.js` (~75 lines) | HTTP entry point — arg parsing, static file serving, `/api/*` dispatch, bootstrap |
| `api/handlers.js` | API endpoint handlers (~35 endpoints) + runtime `companyDir`/`port` state. All reads are file-based; writes go through the MCP client |
| `mcp-client.js` | MCP client — all write operations proxy to the Hive MCP server at `http://127.0.0.1:7891/mcp` (configurable via `HIVE_MCP_URL`). Session management via `mcp-session-id` header |
| `sse.js` | SSE event engine — file watchers on `.company/` subdirectories emit debounced SSE events (`ticket-change`, `chat-change`, `log-change`, `spawn-stream-change`, etc.) with fingerprint dedup |
| `static/app.html`, `static/app.js`, `static/styles.css` | The frontend — GitHub-dark themed SPA with vanilla JS, ~17 view panels, no external libraries. Real files served from disk: **no template literal, no escaping** |
### Data flow

The dashboard is **read-only from the `.company/` directory**. All mutations proxy through the MCP server (`lib/hive_mcp/server.py`), which shims to bash scripts in `lib/tools/`.

### Frontend panels

Vanilla JS SPA with client-side routing. Panels: Dashboard, Board (Kanban), Chat, Meetings, Sprint, Org Chart, Agents, Reports, Logs, Verification, Activity, Rounds, GitHub, Releases, Config (C2 — resolved LLM + last doctor dump + last llm test), Git, Inbox/Outbox, Brief, Worktree Browser. Each panel fetches from `/api/` endpoints and renders from templates.

### SSE live updates

`GET /api/events` opens an SSE connection. File watchers on `.company/` subdirectories fire events with debounce windows (500ms default, 50ms for spawn/cycle/log streams). Fingerprint dedup skips re-rendering byte-identical data to preserve scroll position.

## Key conventions

- **No external dependencies** — adding one requires updating `package.json` and justifying the need.
- **No build step** — static files are edited and served directly.
- **The SPA is real files under `static/`** — edit `app.js` /
  `styles.css` / `app.html` directly; `node --check static/app.js`
  validates exactly what the browser receives (no template-literal
  round-trip to reason about).
- **All API responses are JSON** (GET/POST) except `/api/events` (SSE) and the root route (HTML).
- **Ticket statuses/types/priorities**: see root `AGENTS.md` for the full list.
- **MCP write methods:** `ticket_create`, `ticket_move`, `ticket_comment`, `ticket_approve`, `inbox_write`, `gates_run`, `gates_check`

## Common operations

### Run the dashboard
```bash
node server.js --dir .company --port 3456
```

### Add an API endpoint
1. Add a handler in `handleApi()` in `api/handlers.js`
2. Add a corresponding frontend panel or update an existing one in `static/app.js`
### Add an SSE event type
1. Extend the file watcher in `sse.js`
2. Add a client-side event listener in the SPA's `connectEvents()` function
### Modify the frontend
- CSS is `static/styles.css`
- JS SPA logic is `static/app.js`
- Markup is `static/app.html`
- Templates are string interpolation in JS functions
## See also

- Parent `AGENTS.md` — Framework-level architecture, common commands
- `lib/AGENTS.md` — Cross-module data flow, module map
