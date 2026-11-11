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
- **An MCP server** (~48 tools) that the dashboard, the CLI, and spawned
  agents all share. Structured tool calls instead of long bash
  invocations — saves output tokens (the slow part on local llama.cpp).
- **Strict quality gates**: deterministic gates (tests, lint, security,
  contract compliance) plus an adversarial verification IC review. Tickets
  cannot reach `done/` without explicit IC approval with rationale.

## Get Hive

Latest release: **v0.17.0**.

**One-liner** (fetches the latest release tarball from this repo's
GitHub releases, verifies its sha256 sidecar, and installs to
`~/.local/share/hive`):

```bash
curl -fsSL https://raw.githubusercontent.com/fredjt/hive/main/packaging/install-online.sh | sh
```

**Release tarball** (no git required):

1. Download `hive-0.17.0.tar.gz` and its `hive-0.17.0.sha256` sidecar
   from the [releases page](https://github.com/fredjt/hive/releases).
2. Verify the download before installing:
   `sha256sum -c hive-0.17.0.sha256` (the sidecar carries the expected hash).
   A full audit is one command: `bash packaging/verify-release.sh v0.14.0`
   (verifies the tarball against the release's cut receipt too).
3. Extract and install:

   ```bash
   tar xzf hive-0.17.0.tar.gz
   bash hive-0.17.0/packaging/install.sh --copy-to ~/.local/share/hive
   ```

**From source**:

```bash
git clone https://github.com/fredjt/hive.git
bash hive/packaging/install.sh --copy-to ~/.local/share/hive
```

Then point Hive at your LLM and run the doctor gate — it must pass
before you point a project at the install. The installer created
`~/.config/hive/config.toml` with every key defaulted; the only section
that has no working default is `[llm]`:

```bash
hive config edit    # set provider/model/base_url/api_key in [llm] (examples are commented in)
hive doctor         # gate: all checks green
```

One-shot runs can export `HIVE_PROVIDER`, `HIVE_MODEL`, `HIVE_API_BASE`
and `HIVE_API_KEY` instead of the config file.

The installer copies the tree to `~/.local/share/hive`, creates a Python
venv there with the required packages, and symlinks `~/.local/bin/hive`
to the launcher. Add `~/.local/bin` to your PATH if it isn't already.

Dependencies: Python 3.11+ with `mcp`, `fastapi`, `uvicorn`, `httpx` (auto-
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
        │  ~48 tools             │
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
See `MCP-TOOLS.md` for the MCP tool catalog. See `LLM-PROVIDERS.md`
for the `[llm]` config and the provider registry.

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
- `share/docs/` — this README + ARCHITECTURE.md + CLI.md + MCP-TOOLS.md + LLM-PROVIDERS.md

## Status

Working. Started life as an extraction of the company-skill prototype
and has since grown into a standalone framework.
