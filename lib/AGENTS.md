# AGENTS.md

This file documents the `this directory` area of the Hive project — what it does, how it works, and the conventions for extending it.

## What lives here

`lib/` is the runtime core of the Hive framework — a virtual company orchestration system that spawns AI agents as departmental roles. It contains five major modules plus scaffold templates, each with its own AGENTS.md for deeper detail:

```
lib/
  hive_state/            THE state layer — the only code that reads/writes .company/
                         JSON: per-file flock, atomic tmp+os.replace, schemas (Python)
  hive_config.py         THE config reader (G5) — one TOML parser + precedence;
                         bash reads config only via tools/config-get.sh
  hive_mcp/              MCP server — ~48 tools wrapping bash scripts (Python/FastMCP)
    shell.py             Subprocess bridge — env setup, path resolution, JSON parsing
    tools/               ~13 tool modules (init, tickets, chat, verification, ...)
  hive_cli/              CLI dispatcher — argparse → handler modules → MCP client (Python)
    mcp_client.py        Auto-selecting MCP client (HTTP → in-process; lazy SDK import)
    commands/            One module per CLI subcommand (~17 handlers)
  tools/                 ~80 bash scripts (~11.2k lines) — the operational engine
  core/                  ~5 foundational scripts — agent-spawn, orchestrator, init, hooks
  agents/                ~25 agent prompt files — role definitions for each IC/VP
  dashboard/             Node.js SPA (server.js entry + api/ + static/ frontend) — web UI, zero dependencies
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
| [`hive_mcp/tools/`](hive_mcp/tools/) | ~13 Python tool modules | Each tool is a thin wrapper around a bash script |
| [`hive_cli/`](hive_cli/) | CLI dispatcher | Adding a new CLI subcommand, fixing CLI behavior |
| [`hive_cli/commands/`](hive_cli/commands/) | Per-subcommand handlers | Each file = one `hive <subcommand>` entry |
| [`tools/`](tools/) | ~80 bash scripts | Adding a new operation, fixing orchestration logic, modifying state handling |
| [`core/`](core/) | ~5 foundational scripts | Changing agent spawning, workspace init, PreToolUse hooks |
| [`agents/`](agents/) | ~25 prompt files | Adding a new role, modifying agent behavior, updating procedures |
| [`dashboard/`](dashboard/) | Node.js SPA + server | Modifying the web UI, adding API endpoints, changing SSE events |
| [`templates/`](templates/) | GitHub scaffolding | Modifying CI workflows, PR templates, release publishers |

## How writes reach `.company/`

G1: **one state layer**. `lib/hive_state/` is the ONLY code permitted to
read or write `.company/` JSON. Every other layer (MCP tools, CLI, bash
scripts, the sprint-lifecycle) reaches it the same way: bash scripts call
the `hs` helper (`lib/tools/hive-state.sh` → `python -m hive_state <op>`),
Python code imports the `hive_state` package. Per-file `flock`, one
atomic tmp+`os.replace` convention, and schema validation on every write
live in the state layer — no bash script keeps a JSON mutation of its own.

Write paths into the state layer (all converge on the same ops):

1. **MCP tools** — `hive_mcp/tools/*.py` → `shell.py.run()` → `lib/tools/*.sh` → `hs`
2. **CLI commands** — `hive_cli/commands/*.py` → either MCP client (Style A) or direct bash subprocess (Style B)
3. **Sprint-lifecycle** — `lib/tools/sprint-lifecycle.sh` → directly calls `lib/tools/*.sh` scripts (bypasses MCP) → `hs`

Agents spawned by the sprint-lifecycle **never write `.company/` directly**. They read state, act on the project files (source code, docs, config), and signal completion via exit codes. The sprint-lifecycle then moves tickets and triggers the next stage.

## See also

- [`core/AGENTS.md`](core/) — Foundational scripts, agent spawning, PreToolUse hooks
- [`tools/AGENTS.md`](tools/) — Bash scripts by category, conventions, state files, serialization
- [`hive_mcp/tools/AGENTS.md`](hive_mcp/tools/) — Tool modules, shell.py bridge, adding tools
- [`hive_cli/AGENTS.md`](hive_cli/) — CLI dispatcher, handler patterns, mcp_client
- [`hive_cli/commands/AGENTS.md`](hive_cli/commands/) — Per-subcommand handler patterns
- [`agents/AGENTS.md`](agents/) — Agent prompt architecture, role hierarchy
- [`dashboard/AGENTS.md`](dashboard/) — Node.js SPA architecture, SSE, API endpoints
- [`templates/AGENTS.md`](templates/) — GitHub PR template, CI workflows, release publishers
- Parent `AGENTS.md` — Framework-level architecture, installation, common commands
