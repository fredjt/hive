# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is Hive

Hive is a standalone framework for orchestrating a swarm of Claude Code agents as a virtual company on a software project. It runs as its own application (not a Claude Code skill) using Claude Code as its LLM backend.

**Version:** 0.5.15

## Directory layout

```
bin/hive                              CLI launcher (bash)
lib/                                  Runtime core (see lib/CLAUDE.md for details)
  hive_mcp/                           MCP server (Python, FastMCP)
  hive_cli/                           CLI dispatcher (Python)
  core/                               Foundational bash scripts
  tools/                              101 bash scripts (~13k lines)
  agents/                             Claude agent prompt files
  dashboard/                          Node.js web dashboard
  templates/                          GitHub PR template, CI/release workflows
packaging/                            Install scripts, build, metadata
share/docs/                           Human-readable documentation
etc/config.defaults.toml              Default configuration
```

Each subdirectory has its own `CLAUDE.md` with deeper detail. See the **See also** section below.

## Key architecture concepts

### Two-root design

- **HIVE_INSTALL_DIR** (~/.local/share/hive): Global install — venv, MCP server, CLI, dashboard, hooks. Read-only from inside agents.
- **HIVE_ROOT** (<project>/.hive/): Per-project tweakable scaffold — agents, tools, templates, config.toml. Created by `hive init` via scaffold-extract.

### Runtime paths

- **PROJECT_ROOT**: The project directory containing `.company/`
- **COMPANY_DIR** ($PROJECT_ROOT/.company): Live runtime state — tickets, chat, roster, sprint state, logs. Agents read freely but MUST NOT write directly; all writes go through MCP tools or bash scripts.

### Two-transport MCP server

The MCP server (`lib/hive_mcp/server.py`) supports two transports:
- **stdio**: Used when spawned Claude agents connect via `--mcp-config`
- **streamable-http** (port 7891): Used by the dashboard and CLI for tool calls

### Sprint-staged lifecycle

The round-runner serially walks through stages:
1. ceo-plan → 2. vp-plan → 2.5 sprint-fill → 3. tdd-red → 4. tdd-green → 5. verify → (loop back to 3) → 6. build-and-test → 7. release-verify → 8. release-publish → (loop back to 6a) → 9. vp-review → archive → next sprint

### Important conventions

- Ticket statuses: `backlog` → `sprint-current` → `in-progress` → `in-review` → `done` → `archive`
- Ticket types: `feature`, `bug`, `improvement`, `chore`, `infrastructure`, `research`
- Priorities: `critical`, `high`, `medium`, `low`
- Agent roles: CEO, VPs (per dept), ICs (engineer, tester, infra, admin), Verification IC, Release IC
- Worktrees: per-ticket at `$PROJECT_ROOT/.worktrees/$TID` on branch `feature/$TID`
- The `.company/` directory is live state treated as a database — agents read freely, writes go through MCP tools or scripts only
- **Derived state**: `roster.json` and per-dept `status.json` are rebuilt every 60s from ticket directories, VP log mtimes, and git worktrees — nothing else writes them

## Common commands

### Installation (from extracted archive)
```bash
bash packaging/install.sh --copy-to ~/.local/share/hive
# Verify:
hive doctor
```

### Per-project usage
```bash
cd /path/to/project
hive init . --brief "Project description"
hive                     # Launches dashboard at http://localhost:3456
hive start               # Starts all daemons (MCP server, round-runner, pulse, orphan-recovery, roster/status rebuild)
hive status              # Show derived department status
hive stop                # Kill all daemon processes
hive ticket list backend # List tickets
```

### MCP server (standalone)
```bash
# HTTP transport (default port 7891)
python -m hive_mcp.server --transport http --host 127.0.0.1 --port 7891
# stdio transport (for Claude agent integration)
python -m hive_mcp.server --transport stdio
```

### CLI (from source, without install)
```bash
PYTHONPATH=lib python -m hive_cli.main --help
```

### Dashboard
```bash
# From source (Node.js, no build step)
node lib/dashboard/server.js --dir .company --port 3456
```

## Configuration

- Defaults: `etc/config.defaults.toml`
- Overrides: `~/.config/hive/config.toml`
- Environment (for daemons): `~/.config/hive/env.sh` — set `ANTHROPIC_BASE_URL`, `ANTHROPIC_API_KEY`, `LLM_TINY_URL`, `CLAUDE_BIN`, etc.

## Python dependencies

`packaging/requirements.txt`: mcp, fastapi, uvicorn[standard], httpx, tomli

## See also

- [`lib/CLAUDE.md`](lib/) — Cross-module map, data flow, serialization points
- [`lib/core/CLAUDE.md`](lib/core/) — 5 foundational scripts (agent-spawn, orchestrator, init, hooks)
- [`lib/tools/CLAUDE.md`](lib/tools/) — 101 bash scripts organized by category, conventions, state files, sprint lifecycle
- [`lib/hive_mcp/tools/CLAUDE.md`](lib/hive_mcp/tools/) — MCP tool modules, Python→bash bridge pattern
- [`lib/hive_cli/CLAUDE.md`](lib/hive_cli/) — CLI dispatcher, command handler patterns
- [`lib/hive_cli/commands/CLAUDE.md`](lib/hive_cli/commands/) — Per-subcommand handler details
- [`lib/agents/CLAUDE.md`](lib/agents/) — Agent prompt architecture, role hierarchy
- [`lib/dashboard/CLAUDE.md`](lib/dashboard/) — Single-file Node.js SPA architecture
- [`lib/templates/CLAUDE.md`](lib/templates/) — GitHub PR template, CI workflows, release publishers
- [`lib/templates/github-workflows/CLAUDE.md`](lib/templates/github-workflows/) — CI and release workflow details
- [`lib/templates/publishers/CLAUDE.md`](lib/templates/publishers/) — Release publisher scripts
- [`packaging/CLAUDE.md`](packaging/) — Installer behavior, build, uninstall
- [`share/docs/CLAUDE.md`](share/docs/) — Documentation corpus, cross-references
