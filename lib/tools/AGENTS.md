# AGENTS.md

This file documents the `this directory` area of the Hive project — what it does, how it works, and the conventions for extending it.

## What this directory is

These scripts are the **runtime core** of Hive. They are called by the MCP server (`lib/hive_mcp/`) as tool implementations, by the sprint-lifecycle as orchestration steps, and by daemons as background workers. They read/write JSON state in `.company/` and never invoke an agent directly (that goes through `lib/core/agent-spawn.sh`).

## Script categories

### Lifecycle orchestration (the engine)
| Script | Role |
|---|---|
| `sprint-lifecycle.sh` | **The single driver.** Picks tickets from sprint-current one at a time, runs their full lifecycle (tester → engineer → gates → verifier), then picks the next. Handles CEO/VP planning, sprint-fill, release pipeline, and archive. Replaces the old round-runner + stage-advance split. |
| `sprint-state.sh` | Sourced library for reading/writing `sprints/current.json`. Manages stage transitions, loop iteration counters, release retry counts, ticket lists. All writes are flock-protected atomic jq operations. |
| `round-promote.sh` | Archives current round, promotes next-plan. Called by sprint-lifecycle at sprint boundaries. |
| `round-enqueue.sh` | Enqueues entries into `current-round.json`. |
| `round-status.sh` | Reports current round state. |
| `round-promote.sh` | Archives current round, called by sprint-lifecycle on sprint boundary. |

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
| `gh-pr-reject.sh` | **Deprecation shim** — unconditionally exits 4. PR close now happens via `gh-pr-close.sh` as a scripted side-effect of ticket rejection (see `lib/hive_mcp/tools/github.py`). |
| `gh-push-branch.sh` | Pushes a feature branch. |
| `gh-issue-*.sh` | Poll, comment, close GitHub issues linked to tickets. |
| `gh-init.sh` | One-time GitHub repo setup. |

### Verification & quality gates
| Script | Role |
|---|---|
| `verification-gates.sh` | **Hard gate, not advisory.** Runs 7 checks against in-review tickets: worktree exists, branch has commits, TDD-order (first commit touches test), tests exist, tests pass, lint passes, security audit clean. Called synchronously by sprint-lifecycle after engineer exits. Returns exit 0 (pass), 1 (skip_tdd_red), or 2 (gates fail). |
| `release-test-suite.sh` | Runs the project's test suite during build-and-test stage. |
| `release-gates.sh` | Pre-release gate checks. |
| `release-publish.sh` / `release-publish-runner.sh` | Publishes a release (tarball, tag, publish). |
| `release-verdict-write.sh` | Writes the release verification verdict. |

### Sprint management
| Script | Role |
|---|---|
| `sprint-fill.sh` | Promotes backlog tickets into sprint-current, capped at MAX_ACTIVE per dept. Called by sprint-lifecycle after vp-plan. |
| `sprint-progress.sh` | Shows sprint ticket progression. |
| `sprint-direction-write.sh` | Writes the CEO's direction document. |
| `prioritize-backlog.sh` | Smart-prioritizes backlog tickets using small-model. |
| `sprint-archive-done.sh` | Archives done tickets at sprint boundary. |

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
| `load-agent-prompt.sh` | Loads an agent's `.md` prompt file with framework primer. |

### Background daemons
| Script | Role |
|---|---|
| (none) | Background daemon removed; all orchestration now flows through `sprint-lifecycle.sh`. |

### Infrastructure
| Script | Role |
|---|---|
| `git-worktree.sh` | Creates/manages git worktrees for tickets. |
| `company-stop.sh` | Kills all company processes for a project. Pattern-kill based. |
| `company-start.sh` | Starts all daemons (MCP server, sprint-lifecycle, pulse). |
| `check-company-dir.sh` | Verifies .company/ is a real directory, not a broken symlink. |
| `log-rotate.sh` | Compresses oversized log files. |
| `dept-helpers.sh` | Department utility functions (is_admin_dept, etc.). |
| `ticket-paths.sh` | Resolves ticket paths for a department. |

## How writes reach `.company/`

There are exactly three paths by which `.company/` state is mutated:

1. **MCP tools** — `hive_mcp/tools/*.py` → `shell.py.run()` → `lib/tools/*.sh` → atomic tmp+mv
2. **CLI commands** — `hive_cli/commands/*.py` → either MCP client (Style A) or direct bash subprocess (Style B)
3. **Sprint-lifecycle** — `lib/tools/sprint-lifecycle.sh` → directly calls `lib/tools/*.sh` scripts (bypasses MCP)

Agents spawned by the sprint-lifecycle **never write `.company/` directly**. They read state, act on the project files (source code, docs, config), and signal completion via exit codes. The sprint-lifecycle then moves tickets and triggers the next stage.

## Sprint lifecycle overview

```
CEO plan → VP plan → sprint-fill → [ticket loop] → build-and-test → release-verify → release-publish → VP review → archive

Ticket loop (one ticket at a time):
  Pick ticket from sprint-current
  → Tester (TDD-RED) → Engineer (TDD-GREEN) → Gates → Verifier
  → Done (→ next ticket) or Bounced (re-enqueue or backlog)
```

## See also

- [`core/AGENTS.md`](core/) — Foundational scripts, agent spawning, PreToolUse hooks
- [`hive_mcp/tools/AGENTS.md`](hive_mcp/tools/) — Tool modules, shell.py bridge, adding tools
- [`hive_cli/AGENTS.md`](hive_cli/) — CLI dispatcher, handler patterns, mcp_client
- [`hive_cli/commands/AGENTS.md`](hive_cli/commands/) — Per-subcommand handler patterns
- [`agents/AGENTS.md`](agents/) — Agent prompt architecture, role hierarchy
- [`dashboard/AGENTS.md`](dashboard/) — Node.js SPA architecture, SSE, API endpoints
- [`templates/AGENTS.md`](templates/) — GitHub PR template, CI workflows, release publishers
- Parent `AGENTS.md` — Framework-level architecture, installation, common commands
