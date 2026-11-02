# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What lives here

`lib/` is the runtime core of the Hive framework — a virtual company orchestration system that spawns Claude Code agents as departmental roles. It contains five major modules plus scaffold templates, each with its own CLAUDE.md for deeper detail:

```
lib/
  hive_mcp/              MCP server — ~55 tools wrapping bash scripts (Python/FastMCP)
    shell.py             Subprocess bridge — env setup, path resolution, JSON parsing
    tools/               17 domain modules (init, tickets, chat, verification, ...)
  hive_cli/              CLI dispatcher — argparse → handler modules → MCP client (Python)
    mcp_client.py        Auto-selecting MCP client (HTTP → stdio fallback)
    commands/            One module per CLI subcommand (20 handlers)
  tools/                 101 bash scripts (~13k lines) — the operational engine
  core/                  5 foundational scripts — agent-spawn, orchestrator, init, hooks
  agents/                24 Claude agent prompt files — role definitions for each IC/VP
  dashboard/             Single-file Node.js SPA (~5800 lines) — web UI, zero dependencies
  templates/             GitHub PR template, CI/release workflows, release publishers
```

## Cross-module data flow

```
Browser ←── SSE ───────────────────────────→ dashboard/server.js
Browser ←── API JSON ──────────────────────→ dashboard/server.js
Browser ←── API write ─────────────────────→ dashboard/server.js ──┐
CLI   ←── JSON output ─────────────────────→ hive_cli/commands/*   │
Agents ←── MCP tools ───────────────────────→ MCP server           │
                                                              │
All writes converge on:                                         │
  MCP server → shell.py → lib/tools/*.sh → .company/ JSON files
                                                              │
All reads come from:                                            │
  .company/ files ← dashboard, CLI, agents (direct filesystem)
```

The MCP server (`hive_mcp/`) is the write gateway. Every MCP tool calls `shell.py.run("script-name", ...)` which resolves the script path, sets up environment, and executes the corresponding `lib/tools/script-name.sh`.

## Module map — where to look

| Subdirectory | What it does | When to work here |
|---|---|---|
| [`hive_mcp/`](hive_mcp/) | MCP server + tool modules | Adding a new MCP tool, modifying tool behavior, changing transport |
| [`hive_mcp/tools/`](hive_mcp/tools/) | 17 Python tool modules | Each tool is a thin wrapper around a bash script |
| [`hive_cli/`](hive_cli/) | CLI dispatcher | Adding a new CLI subcommand, fixing CLI behavior |
| [`hive_cli/commands/`](hive_cli/commands/) | Per-subcommand handlers | Each file = one `hive <subcommand>` entry |
| [`tools/`](tools/) | 101 bash scripts | Adding a new operation, fixing orchestration logic, modifying state handling |
| [`core/`](core/) | 5 foundational scripts | Changing agent spawning, workspace init, PreToolUse hooks |
| [`agents/`](agents/) | 24 prompt files | Adding a new role, modifying agent behavior, updating procedures |
| [`dashboard/`](dashboard/) | Node.js SPA + server | Modifying the web UI, adding API endpoints, changing SSE events |
| [`templates/`](templates/) | GitHub scaffolding | Modifying CI workflows, PR templates, release publishers |

## How writes reach `.company/`

There are exactly three paths by which `.company/` state is mutated:

1. **MCP tools** — `hive_mcp/tools/*.py` → `shell.py.run()` → `lib/tools/*.sh` → atomic tmp+mv
2. **CLI commands** — `hive_cli/commands/*.py` → either MCP client (Style A) or direct bash subprocess (Style B)
3. **Sprint-lifecycle** — `lib/tools/sprint-lifecycle.sh` → directly calls `lib/tools/*.sh` scripts (bypasses MCP)

Agents spawned by the sprint-lifecycle **never write `.company/` directly**. They read state, act on the project files (source code, docs, config), and signal completion via exit codes. The sprint-lifecycle then moves tickets and triggers the next stage.

## See also

- [`core/CLAUDE.md`](core/) — Foundational scripts, agent spawning, PreToolUse hooks
- [`tools/CLAUDE.md`](tools/) — Bash scripts by category, conventions, state files, serialization
- [`hive_mcp/tools/CLAUDE.md`](hive_mcp/tools/) — Tool modules, shell.py bridge, adding tools
- [`hive_cli/CLAUDE.md`](hive_cli/) — CLI dispatcher, handler patterns, mcp_client
- [`hive_cli/commands/CLAUDE.md`](hive_cli/commands/) — Per-subcommand handler patterns
- [`agents/CLAUDE.md`](agents/) — Agent prompt architecture, role hierarchy
- [`dashboard/CLAUDE.md`](dashboard/) — Node.js SPA architecture, SSE, API endpoints
- [`templates/CLAUDE.md`](templates/) — GitHub PR template, CI workflows, release publishers
- Parent `CLAUDE.md` — Framework-level architecture, installation, common commands
