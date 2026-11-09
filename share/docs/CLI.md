# Hive CLI Reference

Bare `hive` (no args) starts the MCP server + dashboard and opens a browser.

## Project lifecycle

```
hive init <project-dir> [--brief "..." | --brief-file FILE] [--auto] [--name NAME]
hive start [project-dir]
hive stop  [project-dir]
hive status [project-dir] [--dept DEPT] [--json]
hive dashboard [project-dir] [--port N] [--no-open]
```

`hive init` creates `.company/`, adds it to `.gitignore`, writes the manifest
and brief, initializes chat channels and meeting schedule.

`hive start` spawns all daemons — VP loops, sprint-fill, sprint-archive,
sprint-lifecycle, roster-rebuild, status-rebuild, verification-gates,
verification-runner, worktree-merger, dashboard. PIDs recorded in
`.company/processes.json`.
Repo containment: a hive company must be the top level of its own git
repo. `hive init` and `hive start` refuse to operate in a directory
nested inside another repository (worktrees would branch off the
outer repo and git operations would act on the outer project) - set
`HIVE_ALLOW_NESTED_REPO=1` to override deliberately. `hive start`
also refuses a `.company/` without the init artifacts (a missing
`manifest.json` means the directory was never initialized).


`hive status` reads the derived `status.json` files and prints per-department
health, ticket counts, velocity, and stuck-ticket count.

## Tickets

```
hive ticket create <dept> <type> <priority> <title> <description> [--created-by NAME] [--source SRC] [--estimate EST]
hive ticket get <dept> <ticket-id>
hive ticket list <dept> [status]
hive ticket move <dept> <ticket-id> <new-status> <comment> <author> [reason] [--force]
hive ticket claim <dept> <ticket-id> <role> <agent-id> <agent-name> [--worktree-path PATH]
hive ticket comment <dept> <ticket-id> <author> <comment>
hive ticket approve <dept> <ticket-id> <verifier> <rationale>
```

`type`: `bug | feature | task | research | infrastructure | documentation`
`priority`: `LOW | MEDIUM | HIGH | CRITICAL`
`status`: `backlog | sprint-current | in-progress | in-review | done | archive`
`reason` (for `done`): `implemented | rejected | done`

Moving to `done` is gated — see `ARCHITECTURE.md`. The IC must call
`ticket approve` first with a rationale ≥ 30 chars.

## Chat

```
hive chat send <channel> <author> <department> <message>
hive chat read <channel> [--last N] [--since ISO_TIMESTAMP] [--json]
```

Channels: `general | engineering | leadership | incidents | dept-<name>`

## Agents

```
hive agent spawn-ceo [project-dir] [--mode FRESH|RESUME]
hive agent run-vp <dept> [project-dir]
```

(There is no `agent verify` subcommand: verification ICs are spawned by
sprint-lifecycle during the verify stage, not on demand.)

All agent spawns route through `lib/core/agent-spawn.sh`, which auto-injects
the MCP config so the spawned agent can call hive tools.

## Quality gates

```
hive gates run <dept> [project-dir]
hive gates check <dept> <ticket-id>
hive gates status              # gate state for every in-review ticket (debug stuck ones)
```

`gates run` runs deterministic gates against in-review tickets in `dept`. Failed
gates bounce tickets back to in-progress with a comment.

`gates check` reads the gate report for a single ticket.

## Backlog

```
hive backlog prioritize <dept> [--limit N]
```

Returns the top N backlog tickets. Uses the small CPU model to break ties at
equal priority. Falls back to deterministic priority+age order.

## Direct MCP tool invocation

```
hive tool <tool-name> [--arg key=value ...]
hive tools [--json]
```

`hive tool` calls a single MCP tool with the given args. Values that look
like JSON (numbers, bools, lists) are parsed; everything else is a string.

`hive tools` lists all ~50 registered tools.

## Config

```
hive config show
hive config get <key>             # e.g. mcp.port
hive config set <key> <value>
hive config edit                  # opens ~/.config/hive/config.toml in $EDITOR
hive config path                  # print the effective user-config path and its state
```

Config layers: `etc/config.defaults.toml` (shipped) → `~/.config/hive/config.toml`
(your overrides).

`set` types the value from the key's type in the defaults schema: ints and
bools go bare, strings are quoted, and a **list-typed** key (e.g.
`tdd.applies_to_agents`) takes a JSON array or a comma-separated value and is
written as a real TOML array — so those keys stop being edit-only:

```bash
hive config set tdd.applies_to_agents '["ic-engineer", "ic-infra"]'
hive config set tdd.applies_to_agents 'ic-engineer, ic-infra'   # same result
```

## LLM endpoint

```
hive llm stats                   # windowed call/token/cost totals (--window 24h|1h)
hive llm tail [N]                # tail recent LLM calls
hive llm test                    # one-shot round trip through the configured endpoint
```

## GitHub

```
hive gh status                   # gh CLI / auth / remote / default branch
hive gh pr open <branch> <ticket>   # open a PR for a branch
hive gh pr merge <branch> [--method merge|squash|rebase]  # merge an open PR
hive gh push enable               # per-company opt-in: allow pushes / PRs / GitHub releases
hive gh push disable              # revoke the opt-in (pushes become soft no-ops)
```

## Releases

```
hive release start               # drive the release pipeline through enqueue
hive release status              # print a release record
hive release list                # list recent releases
hive release publish GO|NO-GO v<ver> <rationale>   # the release-ic commit
hive release abort               # mark a release failed and file an IT ticket
```

## Operations

```
hive restart [<project-dir>]      # stop daemons, then start them again
hive killall                       # SIGKILL every process the hive start tree owns
```

The `hive round` subtree (round queue: status/next/history/get/enqueue/
advance/promote) was retired in 0.10 — the round queue it served is gone.
`rounds/current-round.json` is now an append-only activity log written by
the sprint lifecycle, and the dashboard Rounds panel reads it directly.

## Diagnostics

```
hive doctor                       # check agent harness, jq, git, node, venv, MCP server, LLM provider
hive doctor --deep                # + probe LLM endpoint reachability (uses network)
hive doctor --json                # machine-readable dump of every check
hive selftest [<project-dir>]     # probe the agent spawn path for real (no LLM call)
hive version
```

`hive doctor` also verifies the **installed set** (T3, 0.13): it reads
the installed `MANIFEST.txt` (the tarball's own bill of materials —
shipped to the install since 0.13) against the shared required-set
constant `packaging/required-entries.list`, and fails naming each
missing required entry or foreign runtime-state entry. A dev install
(from a git checkout, where the build generates the manifest) gets a
WARN, not a FAIL.

…and the **venv lock** (T1, 0.14): the installed venv must resolve to
the versions the release locked (`packaging/requirements.lock.txt` — the
reproducible Python environment). A drifted package is named with the
locked and the installed version; an install without a lock (pre-0.14)
reports the resolved versions without a FAIL. Refresh the lock when
`requirements.txt` changes: `bash packaging/refresh-venv-lock.sh`
(see PACKAGING.md, "The venv lock").
