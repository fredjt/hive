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
hive gh pr <create|list|view>    # PR operations through the gh CLI
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
hive round status                  # current round + cursor position
hive round next                    # the CEO's pending next-plan.json
hive round history                 # last N completed rounds (with cost/tokens)
hive round get <round-id>          # one historical round by id
hive round enqueue                # append an entry to the round queue
hive round advance                # force-skip the running entry (admin/debug)
hive round promote                # archive current round, promote next
```

## Diagnostics

```
hive doctor                       # check agent harness, jq, git, node, venv, MCP server, LLM provider
hive doctor --deep                # + probe LLM endpoint reachability (uses network)
hive version
```
