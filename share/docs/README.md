# Hive 🐝

A standalone framework for orchestrating a swarm of AI agents as a
virtual company on a software project. Hive's agent runtime is the
built-in agent harness — plain Python that calls LLM provider APIs
directly and reaches the hive MCP server over localhost HTTP — so no
external agent CLI is installed, invoked, or required.

## What it is

- **A virtual company** of agents (CEO, VPs of HR/IT/R&D/Marketing, dynamic
  department VPs, ICs, verification ICs) that build and maintain a project.
- **A workspace at `.company/`** in your project root that holds tickets,
  chat channels, sprint state, derived status, logs.
- **A web dashboard** (Node.js, GitHub-dark theme) for monitoring and control
  — see all departments, ticket boards, agent activity, run/stop the company,
  approve verification, etc.
- **An MCP server** (~55 tools) that the dashboard, the CLI, and spawned
  agents all share. Structured tool calls instead of long bash
  invocations — saves output tokens (the slow part on local llama.cpp).
- **Strict quality gates**: deterministic gates (tests, lint, security,
  contract compliance) plus an adversarial verification IC review. Tickets
  cannot reach `done/` without explicit IC approval with rationale.

## Install

```bash
bash ~/.local/share/hive/packaging/install.sh
hive doctor                           # verify
```

The installer creates a Python venv at `~/.local/share/hive/venv` and
symlinks `~/.local/bin/hive` to the launcher. Add `~/.local/bin` to your
PATH if it isn't already.

Dependencies: Python 3.10+ with `mcp`, `fastapi`, `uvicorn`, `httpx` (auto-
installed in the venv); `git`, `jq`, `node` for the
dashboard, `bash`.

## Quick start

```bash
# Initialize a workspace in your project
cd /path/to/project
hive init . --brief "Build a CLI todo app with auth and a sync server"

# Open the dashboard (in browser)
hive

# Or run headless
hive start                   # starts all daemons
hive status                  # see derived dept status
hive ticket list backend     # list tickets
hive stop
```

## Architecture (one-line tour)

```
┌─────────────┐  ┌─────────────────────────┐  ┌──────────────────┐
│  Dashboard  │  │   hive (CLI - Python)   │  │ Spawned agents   │
│  (Node.js)  │  │                         │  │ (agent harness)  │
└──────┬──────┘  └────────────┬────────────┘  └────────┬─────────┘
       │ HTTP                  │ HTTP / stdio          │ HTTP (MCP)
       └────────────┬──────────┴───────────────────────┘
                    ▼
        ┌────────────────────────┐
        │  Hive MCP server       │  (Python, FastMCP)
        │  ~55 tools             │
        └───────────┬────────────┘
                    │ subprocess
                    ▼
        ┌────────────────────────┐
        │  Bash tool scripts     │   tickets, chat, gates,
        │  lib/tools/*.sh        │   roster-rebuild, etc.
        └───────────┬────────────┘
                    ▼
        ┌────────────────────────┐
        │  .company/ workspace   │   the canonical state
        └────────────────────────┘
```

See `ARCHITECTURE.md` for details. See `CLI.md` for command reference.
See `MCP-TOOLS.md` for the MCP tool catalog.

## Why MCP

Spawned agents see hive's tools via the MCP server (wired by
automatically by `lib/core/agent-spawn.sh`). Instead of generating multi-line
bash invocations like:

```bash
bash $SKILL_DIR/tools/ticket-claim.sh DEPT TID IC-Engineer ic-TID "name" /path
```

agents emit structured tool calls:

```json
{"name": "ticket_claim", "arguments": {"dept": "...", "ticket_id": "..."}}
```

For a 25 t/s local llama.cpp setup this is a meaningful output-token reduction.

## Quality gates

A ticket cannot move to `done/` unless **all** of these are true:

1. Deterministic gates passed (tests exist, tests pass, linter passes,
   security audit clean, branch has commits beyond main, worktree exists)
2. A verification IC has explicitly approved via `ticket-approve.sh` with a
   rationale ≥ 30 characters describing what was actually checked

The `--force` flag bypasses gates but writes to `logs/forced-promotions.log`
for audit. There is no other way out.

## Backward compatibility

Hive is not a skill and never ships one — `hive init` is the entry
point for standing up a company on a project.

## Files / layout

- `bin/hive` — CLI launcher
- `lib/hive_mcp/` — MCP server (Python)
- `lib/hive_cli/` — CLI dispatcher (Python)
- `lib/core/` — bash glue (agent-spawn, init, orchestrator, hook)
- `lib/tools/` — bash tool scripts
- `lib/agents/` — agent prompts
- `lib/dashboard/` — Node.js web dashboard
- `etc/config.defaults.toml` — default config
- `~/.config/hive/config.toml` — your overrides
- `packaging/{install,uninstall}.sh` — setup scripts
- `share/docs/` — this README + ARCHITECTURE.md + CLI.md + MCP-TOOLS.md

## Status

Working. Started life as an extraction of the company-skill prototype
and has since grown into a standalone framework.
