# Hive Architecture

## Components and responsibilities

| Component | Language | Responsibility |
|-----------|----------|----------------|
| `bin/hive` | Bash | Tiny launcher that resolves the venv and execs `hive_cli.main`. |
| `lib/hive_cli/` | Python | argparse CLI, one module per top-level subcommand. Calls MCP tools via `mcp_client.py`. |
| `lib/hive_mcp/` | Python | FastMCP server exposing ~55 tools. Each tool wraps a bash script via `shell.py`. Two transports: stdio (for spawned agents) and HTTP (for dashboard + CLI). |
| `lib/core/agent-spawn.sh` | Bash | Wrapper around the agent harness. Injects an MCP config (so spawned agents see hive tools) and logs every spawn. |
| `lib/core/init.sh` | Bash | Workspace initialization. Replaces SKILL.md Steps 1-3. |
| `lib/core/orchestrator.sh` | Bash | Initial CEO build + transition to perpetual mode. Replaces SKILL.md Steps 4-7. |
| `lib/tools/*.sh` | Bash | The actual implementations (ticket lifecycle, chat, gates, derived state, sprint mgmt, etc.). Mostly unchanged from the company skill. |
| `lib/agents/*.md` | Markdown | Claude agent prompts (CEO, VPs, ICs, verification, marketing). Each has a `🐝 HIVE MCP TOOLS` header instructing the agent to prefer MCP tools. |
| `lib/dashboard/server.js` | Node.js | Web dashboard with kanban, chat viewer, git browser, verification queue, company start/stop. Reads `.company/` files directly; writes go through MCP. |

## State

Everything that matters lives in **`.company/`** in the project root:

```
.company/
├── manifest.json               # project state, mode (build/perpetual)
├── brief.md                    # the project brief
├── roster.json                 # DERIVED — who's active right now
├── processes.json              # PIDs of running daemons
├── departments/<dept>/
│   ├── status.json             # DERIVED — health, ticket counts, velocity
│   ├── plan.md                 # VP's strategy
│   ├── tickets/
│   │   ├── backlog/*.json
│   │   ├── sprint-current/*.json
│   │   ├── in-progress/*.json
│   │   ├── in-review/*.json
│   │   ├── done/*.json
│   │   └── archive/*.json
│   └── components/<ticket-id>/ # gate results, test output
├── chat/<channel>.md           # message boards
├── meetings/history/*.md       # meeting transcripts
├── inbox/                      # user requests
├── outbox/                     # CEO notifications/approvals
├── logs/                       # daemon + spawn logs
└── research/ongoing/           # R&D findings
```

Two files (`roster.json` and per-dept `status.json`) are **derived** —
rebuilt every 60s by `roster-rebuild.sh` and `status-rebuild.sh` from the
ticket directories, VP log mtimes, and git worktrees. They cannot drift
because nothing else writes them.

## Quality gating

The verification pipeline is a hard gate, not advisory:

```
IC moves ticket to in-review
        │
        ▼
verification-gates.sh runs (60-90s loop, per dept)
   │
   ├── ✗ gate fails → bounce back to in-progress with comment
   │
   └── ✓ all gates pass → mark gates_passed=true on ticket file
                         │
                         ▼
              verification-runner.sh spawns adversarial IC
                         │
                         ▼
              IC runs ticket-approve.sh with rationale (≥ 30 chars)
                         │
                         ├── refuses if gates not passed
                         └── refuses if rationale too short
                         │
                         ▼
              IC runs ticket-move.sh done
                         │
                         ├── refuses if !gates_passed
                         ├── refuses if !verified_by
                         └── allows with --force (logs to forced-promotions.log)
```

Gates: worktree exists, branch has commits beyond main, tests exist for
changed code, tests pass, linter passes, security audit clean. Each
configurable via env vars (`GATE_REQUIRE_TESTS`, etc.).

**Security audit (gate 6) is skip, not pass, when no tool is
installed.** Detection + install hints live in `lib/tools/security-audit.sh`
(one source of truth):

| Stack (manifest) | Audit command | Install |
|---|---|---|
| Node (`package.json`) | `npm audit --audit-level=high` | ships with npm |
| Python (`pyproject.toml`, `requirements.txt`) | `bandit -r -ll` or `pip-audit` | `pip install bandit` |
| Rust (`Cargo.toml`) | `cargo audit` | `cargo install cargo-audit` |
| Go (`go.mod`) | `govulncheck ./...` | `go install golang.org/x/vuln/cmd/govulncheck@latest` |

When the stack's tool is missing the gate records
`status=skip` with a detail that names the stack and the install
command, and the gate log carries an explicit `SKIP security-clean:
SKIPPED - ... NOT scanned (install: ...)` line. The dashboard's gate
output view renders skip (and fail) lines in a highlighted banner so a
skip never reads like a pass. A skip means *never scanned*, not
*scanned clean* — install the tool if the project should be audited.

## Backend abstraction

There is exactly **one** point where Hive talks to the LLM: `agent-spawn.sh`.
It wraps `claude -p` with:
- An MCP config pointing at hive's HTTP MCP server (so spawned agents can call
  `ticket_create`, `chat_send`, etc. as tools)
- `--dangerously-skip-permissions` (spawned agents have no UI to approve)
- `--output-format json` (parseable by callers)
- Logging to `.company/logs/spawns.log`

A future second backend (Anthropic API direct, OpenAI-compatible) would
extend this script. None today.

## Process model

`hive start` (or the dashboard's "Start company" button) launches these
background processes via `lib/tools/company-start.sh`:

| Daemon | Cadence | Job |
|--------|---------|-----|
| CEO | 5 min | Process inbox, manage cycles |
| VP-{HR,IT,R&D,Marketing} | 10-15 min | Department-specific cycles |
| VP-{dynamic depts} | 5 min | Process department tickets, spawn ICs |
| sprint-fill | 2 min | Promote backlog → sprint-current |
| sprint-archive | 30 min | Move done → archive |
| sprint-lifecycle | 30 sec | Detect sprint transitions |
| roster-rebuild | 60 sec | Derive roster.json |
| status-rebuild | 60 sec | Derive all status.json |
| verification-gates | 90 sec | Run quality gates per dept |
| verification-runner | 90 sec | Spawn adversarial verification ICs |
| worktree-merger | 60 sec | Merge done worktrees |
| meeting-runner | continuous | Standup + sprint review at scheduled times |
| dashboard | always | Node.js web UI |

PIDs recorded in `.company/processes.json`. `hive stop` kills them all.

## Rationale: bash + Python + Node

| Layer | Why this language |
|-------|-------------------|
| Tool scripts (ticket-*, chat-*) | Bash. Already worked; rewriting in Python would be busywork. |
| Daemons | Bash. Same. |
| `claude -p` wrapper | Bash. Closest to the binary it wraps. |
| MCP server | Python. SDK is mature (`mcp` package). |
| CLI dispatcher | Python. argparse beats nested case statements. |
| Dashboard | Node.js. Already built (~2000 lines, zero npm deps), reused as-is with surgical mutation tweaks. |

## Why Hive isn't a Claude Code skill anymore

It outgrew the skill format:
- 50+ shell scripts
- 16 agent prompts
- A web dashboard
- Strict quality gates
- A swappable LLM backend
- An MCP server

A skill is meant to add a capability *to* Claude Code; Hive is more like
a system that *uses* Claude Code as a building block. The old skills now
exist as 5-line shims that forward to `hive init`.
