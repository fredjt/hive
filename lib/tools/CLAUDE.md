# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with the `lib/tools/` directory — 101 bash scripts (~13k lines) that form the operational engine of the Hive agent orchestration framework.

## What this directory is

These scripts are the **runtime core** of Hive. They are called by the MCP server (`lib/hive_mcp/`) as tool implementations, by the round-runner as orchestration steps, and by daemons as background workers. They read/write JSON state in `.company/` and never invoke Claude directly (that goes through `lib/core/claude-spawn.sh`).

## Script categories

### Lifecycle orchestration (the engine)
| Script | Role |
|---|---|
| `round-runner.sh` | Single-threaded FIFO drainer. The sole consumer of `claude-spawn.sh`. Singleton-locked via `flock`. Drains `current-round.json`, spawns agents synchronously, handles post-spawn ticket moves and TDD-red attempt caps. |
| `stage-advance.sh` | The brain. Called when a round drains. Reads current sprint stage, decides next stage, runs sync actions (sprint-fill, build-and-test, release-publish), enqueues the next stage's entries. Loops internally through sync stages until landing on a spawn stage. |
| `sprint-state.sh` | Sourced library for reading/writing `sprints/current.json`. Manages stage transitions, loop iteration counters, release retry counts, ticket lists. All writes are flock-protected atomic jq operations. |
| `round-promote.sh` | Archives current round, called by round-runner on drain. |
| `round-enqueue.sh` | Enqueues entries into `current-round.json`. |
| `round-status.sh` | Reports current round state. |
| `stage-enqueue-*.sh` | One per sprint stage — each produces the JSON entries for that stage's round (e.g., `stage-enqueue-tdd-red.sh` creates tester+engineer entries for all sprint tickets). |

### Ticket management
| Script | Role |
|---|---|
| `ticket-move.sh` | **The central ticket mutation point.** Moves a ticket between statuses (backlog → sprint-current → in-progress → in-review → done → archive). Role-based access control: agents cannot write directly; only scripted moves (`HIVE_SCRIPTED_MOVE=1`) or specific roles (Verification-IC, VP) are allowed. Handles git branch detection, PR open/merge/close side-effects, and GitHub issue mirroring. |
| `ticket-paths.sh` | Resolves `.company/departments/<dept>/tickets/` paths for a department. Sourced by ticket scripts. |
| `ticket-create.sh` | Creates a new ticket JSON in the backlog directory. |
| `ticket-get.sh` | Reads a ticket's JSON. |
| `ticket-list.sh` | Lists tickets by department/status. |
| `ticket-approve.sh` | Atomic approval: runs verification gates, stamps `verified_by/verified_at`, moves to done, merges PR. One call. |
| `ticket-bounce.sh` | Atomic bounce: writes rationale, moves in-review → in-progress. |
| `ticket-reject.sh` | Closes a ticket as won't-do. |
| `ticket-claim.sh` | Assigns an unassigned ticket to an IC. |
| `ticket-comment.sh` | Appends a comment to a ticket. |
| `ticket-route-expedited.sh` | Routes high-priority tickets. |
| `done-protector.sh` | Sweeps stale done-duplicates after git checkout/merge. |

### GitHub integration
| Script | Role |
|---|---|
| `gh-helpers.sh` | **Sourced by all gh-*.sh scripts.** Resolves auth (`GITHUB_TOKEN` from `env.sh` or `gh` keyring), remote URL, default branch. Silent on success, one-line errors on stderr. |
| `gh-pr-open.sh` | Opens a PR for a feature branch. |
| `gh-pr-merge.sh` | Merges a PR. Exit 5 = conflict (caller rolls back). |
| `gh-pr-close.sh` | Closes a PR (on rejection). |
| `gh-pr-reject.sh` | Rejects a PR. |
| `gh-push-branch.sh` | Pushes a feature branch. |
| `gh-issue-*.sh` | Poll, comment, close GitHub issues linked to tickets. |
| `gh-init.sh` | One-time GitHub repo setup. |

### Verification & quality gates
| Script | Role |
|---|---|
| `verification-gates.sh` | **Hard gate, not advisory.** Runs 8 checks against in-review tickets: worktree exists, branch has commits, TDD-order (first commit touches test), tests exist, tests pass, lint passes, security audit clean, coverage threshold. Auto-bounces failing tickets to in-progress. Called by the pulse daemon every 60-90s and synchronously after engineer spawns. |
| `release-test-suite.sh` | Runs the project's test suite during build-and-test stage. |
| `release-gates.sh` | Pre-release gate checks. |
| `release-publish.sh` / `release-publish-runner.sh` | Publishes a release (tarball, tag, publish). |
| `release-verdict-write.sh` | Writes the release verification verdict. |

### Initialization & scaffolding
| Script | Role |
|---|---|
| `init-workspace.sh` | Sets up a new project workspace. |
| `init-brief.sh` | Writes the project brief. |
| `init-manifest.sh` | Creates the manifest. |
| `init-chat-channels.sh` | Sets up chat channels. |
| `init-<dept>-scaffold.sh` | Per-department scaffolds (finance, legal, marketing). |
| `scaffold-extract.sh` / `scaffold-update.sh` | Extracts/updates the per-project `.hive/` scaffold from the install. |

### Agent spawning & cycle control
| Script | Role |
|---|---|
| `ic-spawn.sh` | Spawns an individual IC agent (calls `claude-spawn.sh` with agent prompt + assignment). |
| `ic-spawn-validate.sh` | Validates a spawn's prerequisites. |
| `ic-spawn-monitor.sh` / `ic-spawn-diagnostics.sh` | Monitor and diagnose running spawns. |
| `agent-cycle.sh` | Runs a full agent cycle (plan → execute). |
| `load-agent-prompt.sh` | Loads an agent's `.md` prompt file with framework primer. |
| `llm-tiny.sh` | Calls a cheaper/faster LLM endpoint for lightweight tasks. |

### Background daemons
| Script | Role |
|---|---|
| `pulse-daemon.sh` | Runs `verification-gates.sh` every 60-90s per department. Sets `gates_passed=true` on passing tickets. |
| `orphan-recovery.sh` | Bounces stuck in-review tickets that haven't progressed. |
| `roster-rebuild.sh` / `roster-update.sh` | Derives `roster.json` from ticket directories. |
| `status-rebuild.sh` / `status-update.sh` | Derives per-dept `status.json` from tickets, VP logs, worktrees. |
| `narrate-status.sh` | Produces a status narrative for standup. |

### Company lifecycle
| Script | Role |
|---|---|
| `company-start.sh` | Starts all daemons (MCP server, round-runner, pulse, orphan-recovery, etc.). |
| `company-stop.sh` | Stops all daemons. |
| `hive-killall.sh` | Hard kills all Hive processes. |
| `check-company-dir.sh` | Validates `.company/` integrity (symlink loops, corruption). |

### Admin & cross-cutting
| Script | Role |
|---|---|
| `dept-helpers.sh` | **Sourced by stage-enqueue scripts, verification-gates, stage-advance.** Defines admin vs engineering departments. Single source of truth. |
| `prioritize-backlog.sh` | Orders backlog tickets for sprint planning. |
| `sprint-fill.sh` | Promotes backlog tickets to sprint-current (during vp-plan → tdd-red transition). |
| `sprint-archive-done.sh` | Archives done tickets when a sprint ends. |
| `sprint-direction-write.sh` | Writes CEO direction. |
| `sprint-progress.sh` / `sprint-state.sh` | Sprint progress reporting. |
| `meeting-runner.sh` / `meeting-record.sh` | Meeting orchestration. |
| `inbox-process.sh` / `inbox-write.sh` | Agent inbox management. |
| `outbox-write.sh` / `outbox-archive.sh` | Agent outbox management. |
| `research-findings.sh` | Collects research findings. |
| `onboard-generate.sh` | Generates agent onboarding. |
| `migrate-agents.sh` | Migrates agent assignments. |
| `classify-ticket.sh` | Classifies new tickets. |
| `report-write.sh` | Writes reports. |
| `summarize-thread.sh` | Summarizes chat threads. |
| `chat-send.sh` / `chat-read.sh` | Chat operations. |
| `org-chart-rebuild.sh` | Rebuilds org chart. |
| `worktree-merger.sh` | Merges worktree branches. |
| `git-worktree.sh` | Creates/manages per-ticket git worktrees. |
| `cleanup-worktrees.sh` | Health-supervisor cleanup: prunes stale entries, removes rebase helpers (hive-check-rebase-*, ci-rebase-test-*), nested temp worktrees (tmp.*), and detached non-feature worktrees. Runs every 300s via company-health.sh. Configurable via `HIVE_WORKTREE_CLEANUP_INTERVAL`. |
| `log-rotate.sh` | Compresses oversized daemon and spawn/VP logs. When a log exceeds its size cap, it is gzip-compressed to a timestamped `.gz` backup and the active log is truncated (keeps the same inode). Compressed backups are never deleted — retained for debugging. Configurable via `HIVE_LOG_MAX_DAEMON_MB` (default 50) and `HIVE_LOG_MAX_SPAWN_MB` (default 20). |
| `company-health.sh` | Health supervisor: runs every 30s as a background loop. Responsibilities: (1) daemon liveness + restart, (2) derived-state heartbeat (one-time alert per stale file, 5-minute dedup), (3) stale active spawn cleanup, (4) log rotation, (5) stale worktree cleanup, (6) tiny model probe (one-time alert, 5-minute dedup), (7) **disk usage monitoring** (alerts at configurable WARN/CRIT thresholds, dedup'd per threshold), (8) **ticket directory disk reporting** (done/ + archive/ sizes, dedup'd every 5 min). All alerting uses deduplication to prevent repeated noise. Configurable via `HIVE_DISK_USAGE_WARN_PCT` (default 80), `HIVE_DISK_USAGE_CRIT_PCT` (default 90), `HIVE_TICKET_DISK_REPORT_INTERVAL` (default 300). |
| `ic-spawn.sh` | Spawns IC agents. |
| `test-api.sh` / `test-results.sh` | Test API helpers. |
| `company-start.sh` / `company-stop.sh` | Daemon lifecycle. |
| `repair-ticket-counters.sh` / `repair-tracked-company.sh` | Repair utilities for corrupted state. |
| `init-meeting-schedule.sh` | Meeting scheduling setup. |

## Conventions

### Every script
- Uses `set -euo pipefail` (except `gh-helpers.sh` which omits `-e` so callers handle non-zero gracefully).
- Sources shared helpers (`dept-helpers.sh`, `gh-helpers.sh`, `ticket-paths.sh`, `sprint-state.sh`) via `SKILL_DIR/lib/tools/`.
- Resolves `SKILL_DIR` from `HIVE_ROOT` or by walking up from the script's own directory: `$(cd "$(dirname "$0")/../.." && pwd)`.
- Outputs **JSON on stdout** when the result is structured (MCP tools parse this).
- Writes diagnostics and errors to **stderr**.
- Never writes directly to `.company/` files — uses atomic write pattern (write `.tmp`, then `mv`).

### Environment variables
All scripts expect these from the caller (round-runner, MCP server, or daemon):
- `COMPANY_DIR` — path to `.company/` runtime state
- `PROJECT_ROOT` — the project directory containing `.company/`
- `SKILL_DIR` / `HIVE_ROOT` — installation directory (where `lib/tools/` lives)
- `HIVE_ENV_FILE` — path to `~/.config/hive/env.sh` (defaults)

Script-specific:
- `HIVE_ROLE` — role of the caller (used in `ticket-move.sh` for ACL)
- `HIVE_SCRIPTED_MOVE` — set to `1` for system-driven ticket moves (bypasses agent ACL)
- `HIVE_AGENT` — agent name (e.g., `IC-Engineer`, `Verification-IC`)
- `HIVE_DEPT` — department name
- `HIVE_TICKET` — ticket ID
- `GITHUB_TOKEN` — GitHub auth (used by `gh-helpers.sh`)

### State files
All state lives in `.company/` (the "company directory"), treated as a database:
- `.company/departments/<dept>/tickets/<status>/<TID>.json` — ticket state
- `.company/sprints/current.json` — sprint lifecycle state (stage, loop counters)
- `.company/rounds/current-round.json` — current round queue (entries + cursor)
- `.company/rounds/runner.pid` — round-runner PID (singleton lock)
- `.company/rounds/queue-lock` — queue mutation lock
- `.company/logs/*.log` — audit logs per component
- `.company/roster.json` — derived, rebuilt every 60s
- `.company/departments/<dept>/status.json` — derived per-dept status

### Ticket status flow and sprint lifecycle

Ticket status flow: `backlog` → `sprint-current` → `in-progress` → `in-review` → `done` → `archive`
Sprint-staged lifecycle and loop-back paths are covered in the root `CLAUDE.md`.

### Serialization

- **round-runner.sh** is the sole serialization point for agent spawns (singleton `flock` on `runner.pid`).
- **sprint-state.sh** uses `flock` on `sprints/.lock` for state file mutations.
- **Queue operations** use `flock` on `rounds/queue-lock`.
- **ticket-move.sh** does not use locks but relies on atomic tmp+mv writes and is always called from the single-threaded round-runner or MCP server.
- **fd 9 closing** — the round-runner closes fd 9 (`9<&-`) in the child process before exec so Claude subprocesses don't inherit and hold the singleton lock.

### Adding a new tool script
1. Create `lib/tools/<name>.sh` with `set -euo pipefail`.
2. Source shared helpers if needed: `. "$SKILL_DIR/lib/tools/dept-helpers.sh"` or `gh-helpers.sh`.
3. Use `SKILL_DIR` (from `HIVE_ROOT` or path resolution) to find other scripts.
4. Read/write state through `.company/` using atomic tmp+mv pattern.
5. Output JSON on stdout for MCP tools; stderr for diagnostics.
6. If it's a new MCP tool, register it in `lib/hive_mcp/server.py`'s `TOOL_MODULES`.

### Common pitfalls
- **Never call `ticket_move` directly** for approve/bounce/reject — use the atomic tools (`ticket_approve`, `ticket_bounce`, `ticket_reject`).
- **Don't set `set -e` in `gh-helpers.sh`** — callers need to handle non-zero exit codes gracefully.
- **Don't write to `.company/` directly** — all mutations go through scripts.
- **`ticket-move.sh` is the gate** — it enforces role-based ACL, verification evidence requirements, and PR side-effects. If a ticket move isn't going through this script, something is wrong.
- **`verification-gates.sh` is a hard gate** — passing gates does NOT auto-promote to done; the Verification IC must still approve.
- **Admin departments** (hr, it, rd, marketing, legal, finance, executive) are non-code — `dept-helpers.sh` classifies them, and `verification-gates.sh` auto-passes them.

## See also

- Parent `CLAUDE.md` — Framework-level architecture, sprint lifecycle overview
- `lib/CLAUDE.md` — Cross-module data flow, serialization overview
- `lib/core/CLAUDE.md` — Foundational scripts, agent spawning
- `lib/hive_mcp/tools/CLAUDE.md` — MCP tools that call these scripts
