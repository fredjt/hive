# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working within the `lib/hive_mcp/tools/` module.

## Role

This directory contains the MCP tool modules for the Hive framework — a collection of Python modules that expose ~50 tools via the Hive MCP server (`server.py`). Each tool is a thin Python wrapper around a bash script in `<scaffold>/lib/tools/*.sh`. The tools are grouped by domain.

## Structure

```
lib/hive_mcp/tools/
  __init__.py          (empty — namespace package marker)
  init.py              Workspace initialization (init-workspace, init-manifest, init-brief, etc.)
  tickets.py           Ticket lifecycle — create, get, list, move, claim, comment, approve, bounce, reject, route-expedited
  chat.py              Chat channels — send / read messages
  status.py            Derived state — status_get, status_rebuild, roster_get, roster_rebuild
  verification.py      Verification gates — run gates, check gate results
  worktrees.py         Git worktrees — create, merge, list, cleanup, auto-merge
  agents.py            Agent spawning — spawn CEO, run VP cycle, spawn IC
  company.py           Company process control — read-only status (start/stop intentionally excluded)
  inbox.py             Inbox/outbox — file requests, process inbox, write/archive outbox
  sprint.py            Sprint management — fill, progress, prioritize backlog
  rounds.py            Round-runner queue — get current/next, history, enqueue, advance, promote
  reports.py           Report writing — research findings, test results, meeting records
  llm_tiny.py          Small-model helpers — raw llm-tiny, classify-ticket, triage-test-failure, narrate, summarize
  github.py            GitHub integration — status, issue mirror, PR open/merge/reject
  release.py           Release pipeline — verdict, status, history (read-only via MCP)
```

## Tool module pattern

Every module follows the same shape:

```python
from mcp.server.fastmcp import FastMCP
from hive_mcp.shell import run

def register(server: FastMCP) -> None:
    @server.tool()
    def my_tool(arg1: str, arg2: int, company_dir: str | None = None) -> dict:
        """Docstring describes the tool. company_dir is auto-injected by FastMCP."""
        args = [arg1, str(arg2)]
        return run("bash-script-name", *args, company_dir=company_dir).to_dict()
```

Key conventions:
- **`register(server)`** — the only required top-level function. Called by `server.py` during `build_server()`.
- **`company_dir: str | None = None`** — every tool that touches project state accepts this optional kwarg. FastMCP auto-injects it from the MCP session.
- **`run("script-name", *args, company_dir=company_dir).to_dict()`** — the standard return pattern. `run()` shims to `lib/tools/script-name.sh`, handles path resolution, env setup, timeout, and JSON parsing.
- **Docstrings are agent-facing** — they describe args, expected values, and side effects. The spawned Claude agents read these to decide which tool to call.

## When to read from files directly vs. shell out

Most tools delegate to bash scripts via `run()`. However, some tools read `.company/` files directly (no bash script needed):

| Pattern | Example modules |
|---------|----------------|
| `run("script", *args)` → bash script | `tickets`, `chat`, `sprint`, `rounds`, `reports`, `llm_tiny`, `github`, `release` |
| Direct file read (`Path.read_text()`) | `company` (processes.json), `status` (status.json, roster.json), `verification` (gates.json), `github` (gh_issues reads mirror JSON) |
| Hybrid (both) | `release` — reads JSON directly for status/history, uses `run()` for verdict write |

Direct file reads are appropriate when the operation is simple (parse a JSON file, list directory entries) and doesn't need the shared env/path/setup logic that `run()` provides.

## Adding a new tool

1. **Decide the module**: Does the tool fit an existing module's domain? If yes, add to that module's `register()`. If no, create a new file.
2. **Create the bash script**: Add `<project>/.hive/lib/tools/<script-name>.sh` (or the global install equivalent).
3. **Add the tool**: Create the `@server.tool()` function inside `register()`. Follow the pattern above.
4. **Register the module** (new file only): Add the module name to `TOOL_MODULES` in `lib/hive_mcp/server.py`.
5. **Document**: Update this CLAUDE.md's structure table if new.

## Adding a new module

1. Create `lib/hive_mcp/tools/<name>.py` with a `register(server)` function.
2. Add `"<name>"` to `TOOL_MODULES` in `lib/hive_mcp/server.py` (order affects docs/help output only).
3. The module is auto-loaded — no other wiring needed.

## shell.py — the subprocess bridge

`shell.py` is the central bridge between MCP tools and bash scripts. Key concepts:

- **`run(script, *args, company_dir, env, stdin, timeout, parse_json)`** — the universal entry point. Resolves the script path, sets up env vars (`COMPANY_DIR`, `SKILL_DIR`, `HIVE_ROOT`), runs `bash <script>`, parses JSON stdout if possible.
- **`ShellResult` dataclass** — structured result with `ok`, `exit_code`, `stdout`, `stderr`, `parsed`. `.to_dict()` returns the MCP-friendly dict shape.
- **Scaffold resolution order**: `company_dir` argument → `$HIVE_ROOT` env → `$HIVE_INSTALL_DIR/lib/tools`. A long-running MCP server can serve multiple projects because `company_dir` is per-call.

## See also

- Parent `CLAUDE.md` — Framework-level architecture, common commands
- `lib/CLAUDE.md` — Cross-module data flow, module map
- `lib/hive_mcp/server.py` — Server bootstrap, tool module registration
- `lib/hive_cli/CLAUDE.md` — CLI dispatcher that proxies to these tools
- `lib/tools/CLAUDE.md` — Bash scripts called by these tools
