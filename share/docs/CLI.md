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

## Inbox

```
hive inbox file <message> [--type auto|bug|feature|feedback] [--dept auto|<name>]
hive inbox process [--dry-run]
```

`file` writes a user request. With `--type auto` and `--dept auto`, the small
CPU model classifies and routes automatically.

`process` runs the inbox processor (CEO routes pending requests to dept boards).

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
```

Config layers: `etc/config.defaults.toml` (shipped) → `~/.config/hive/config.toml`
(your overrides).

## Diagnostics

```
hive doctor                       # check claude bin, jq, git, node, venv, MCP server
hive version
```
