# AGENTS.md

This file documents the `lib/core/` directory of the Hive project.

## What lives here

`lib/core/` contains the foundational scripts that form Hive's runtime
backbone. Every other layer (MCP tools, agent prompts, CLI, dashboard)
depends on these:

| Script | Role |
|---|---|
| `agent-spawn.sh` | Sole entry point for spawning an agent. Points the harness at the MCP server over HTTP, applies identity env vars, logs spawns, and tees the stream-json event log to `.company/logs/streams/`. |
| `orchestrator.sh` | CEO initial-build launcher. Reads the CEO prompt, appends assignment metadata, invokes `agent-spawn.sh`, then transitions to perpetual mode via `company-start.sh`. |
| `init.sh` | Workspace initialization. Creates `.company/`, manifest, brief, chat channels, meeting schedule, marketing/legal/finance scaffolds, git repo, and GitHub remote. |
| `agent-harness.py` | The built-in agent runtime: plain-Python conversation loop over the configured LLM provider API + MCP tool calls over HTTP, with bash-execution safety limits and a stream-json event log. |
| `block-company-writes.sh` | INERT no-op passthrough (retired in 0.6.0 — kept so stale hook entries from older installs never break tooling). |
| `block-admin-code-writes.sh` | INERT no-op passthrough (retired in 0.6.0 — same reason). |

## Conventions

- All scripts use `set -euo pipefail` (except `agent-spawn.sh`, which omits
  `e` on the main pipeline intentionally — see below).
- Environment variables are sourced from `$HOME/.config/hive/env.sh` when
  present, so daemons spawned via nohup/systemd inherit LLM endpoint config.
- `HIVE_ROOT` defaults to `$HOME/.local/share/hive`; `HIVE_INSTALL_DIR`
  can override the install location.
- `COMPANY_DIR` defaults to `$PROJECT_ROOT/.company` — this is the live
  runtime state directory that agents READ freely but MUST NOT write
  directly.
- All scripts accept `--` to separate flags from positional args.

## agent-spawn.sh — critical details

This is the **only** place the agent harness is invoked. Any changes to
harness invocation, transport config, or agent spawning logic go here.

- **MCP wiring**: The harness is invoked with `--mcp-url
  http://$HIVE_MCP_HOST:$HIVE_MCP_PORT/mcp` (default `127.0.0.1:7891`).
  Spawned agents call hive tools over localhost HTTP.
- **Event log**: The harness writes stream-json events (assistant text,
  tool calls, tool results, final result); the spawn tees them to
  `.company/logs/streams/<id>.jsonl` for dashboard replay.
- **Pipe handling**: Uses `python3 -u .../agent-harness.py ... | tee
  "$STREAM_FILE"` with `exit ${PIPESTATUS[0]:-0}` — pipefail is OFF
  intentionally so the harness's exit code propagates even if tee
  succeeds.
- **Observability**: Each spawn writes an active record to
  `.company/logs/active/<id>.json`, streams to
  `.company/logs/streams/<id>.jsonl`, and appends a cycle summary to
  `.company/logs/cycles.jsonl`.
- **Identity env vars**: Set `HIVE_AGENT`, `HIVE_DEPT`, `HIVE_ROLE`,
  `HIVE_TICKET` before calling — these are consumed by the spawn metadata.
- **Verification model override**: When `HIVE_ROLE` contains
  "verification" (case-insensitive) and `HIVE_VERIFICATION_MODEL` is set,
  `--model "$HIVE_VERIFICATION_MODEL"` is passed to the harness. This lets
  verification agents use a stronger model without changing the default
  for other agents.
- **`--no-mcp` flag**: Spawn without MCP wiring (for prompts that don't
  need the hive server).

## orchestrator.sh — lifecycle transition

This script orchestrates the CEO's initial build and the handoff to
perpetual mode:

1. Validates manifest exists at `.company/manifest.json`.
2. Determines `FRESH` vs `RESUME` mode from manifest's `.mode` field.
3. Reads the CEO prompt from `$HIVE_ROOT/lib/agents/ceo.md`, appends
   assignment metadata (project root, brief path, manifest path, mode,
   auto flag).
4. Invokes `agent-spawn.sh` with the prompt piped via stdin.
5. On `INITIAL_BUILD_COMPLETE` signal: updates manifest to `perpetual`
   mode and `exec`s `company-start.sh`.
6. On failure: exits with path to CEO output log for debugging.

## init.sh — workspace bootstrap

Runs 8 sequential setup steps, each delegated to a script tool in
`$HIVE_ROOT/lib/tools/`:

1. `init-workspace.sh` — creates `.company/` and gitignore entries
2. `init-manifest.sh` — writes manifest
3. `init-brief.sh` — writes project brief
4. `init-chat-channels.sh` — creates chat channels
5. `init-marketing-scaffold.sh` — marketing deliverables
6. `init-legal-scaffold.sh` — privacy, ToS, license inventory
7. `init-finance-scaffold.sh` — budget, runway, unit economics
8. Git init + GitHub remote setup

No per-project scaffold is created: the runtime always resolves scripts,
prompts, and templates from the global install (`$HIVE_ROOT`).

Idempotent: if `.company/` already exists, only fills in missing pieces.

## The retired block-*.sh scripts

`block-company-writes.sh` and `block-admin-code-writes.sh` were PreToolUse
hooks on the retired external agent CLI. Since 0.6.0 nothing registers
them: the agent runtime is the harness (with its own bash-execution safety
limits), and the .company/ write rule is enforced by the prompts plus the
MCP-tool/script-tool layer. Both scripts are exit-0 passthroughs that
drain the hook payload on stdin — old hook entries from 0.5.x installs
point at them and must keep working. `packaging/uninstall.sh` carries a
one-time legacy cleanup that strips those stale entries.

## See also

- Parent `AGENTS.md` — Framework-level architecture, installation, common commands
- `lib/tools/AGENTS.md` — Operational scripts, lifecycle orchestration
- `lib/agents/AGENTS.md` — Agent prompt files loaded by the spawn path
