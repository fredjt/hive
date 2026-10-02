# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository's core directory.

## What lives here

`lib/core/` contains the five foundational bash scripts that form Hive's runtime backbone. Every other layer (MCP tools, agent prompts, CLI, dashboard) depends on these:

| Script | Role |
|---|---|
| `agent-spawn.sh` | Sole entry point for invoking an AI agent. Injects MCP config, applies default flags, logs spawns, streams output to `.company/logs/streams/`. |
| `orchestrator.sh` | CEO initial-build launcher. Reads the CEO prompt, appends assignment metadata, invokes `agent-spawn.sh`, then transitions to perpetual mode via `company-start.sh`. |
| `init.sh` | Workspace initialization. Creates `.company/`, manifest, brief, chat channels, meeting schedule, marketing/legal/finance scaffolds, git repo, and GitHub remote. |
| `block-company-writes.sh` | PreToolUse hook. Blocks Write/Edit/NotebookEdit/Bash mutations targeting `.company/` — agents must use MCP tools or script tools instead. |
| `block-admin-code-writes.sh` | PreToolUse hook. Blocks non-engineering departments from writing application source code (config, docs, build files still allowed). |

## Conventions

- All scripts use `set -euo pipefail` (except `agent-spawn.sh` which omits `e` on the main pipeline intentionally — see below).
- Environment variables are sourced from `$HOME/.config/hive/env.sh` when present (see `agent-spawn.sh` line 37).
- `HIVE_ROOT` defaults to `$HOME/.local/share/hive`; `HIVE_INSTALL_DIR` can override the install location.
- `COMPANY_DIR` defaults to `$PROJECT_ROOT/.company` — this is the live runtime state directory that agents READ freely but MUST NOT write directly.
- All scripts accept `--` to separate flags from positional args.

## agent-spawn.sh — critical details

This is the **only** place the `claude` CLI binary is invoked. Any changes to Claude invocation flags, transport config, or agent spawning logic go here.

- **MCP injection**: Creates a temp JSON file with HTTP transport config pointing to the MCP server (default `127.0.0.1:7891`). Deleted via trap on exit.
- **Flags**: Defaults to `--dangerously-skip-permissions --output-format stream-json --verbose`. The `stream-json` format feeds the dashboard live tool-call and assistant-text events.
- **Pipe handling**: Uses `"$CLAUDE_BIN" -p "$PROMPT" "${CLAUDE_FLAGS[@]}" | tee "$STREAM_FILE"` with `exit ${PIPESTATUS[0]:-0}` — pipefail is OFF intentionally so Claude's exit code propagates even if tee succeeds.
- **Observability**: Each spawn writes an active record to `.company/logs/active/<id>.json`, streams to `.company/logs/streams/<id>.jsonl`, and appends a cycle summary to `.company/logs/cycles.jsonl`.
- **Identity env vars**: Set `HIVE_AGENT`, `HIVE_DEPT`, `HIVE_ROLE`, `HIVE_TICKET` before calling — these are consumed by the spawn metadata and hooks.
- **Verification model override**: When `HIVE_ROLE` contains "verification" (case-insensitive) and `HIVE_VERIFICATION_MODEL` is set, `--model "$HIVE_VERIFICATION_MODEL"` is injected into the Claude CLI flags. This lets verification agents use a stronger model without changing the default for other agents.
- **`--no-mcp` flag**: Skips MCP config injection (for spawns that don't need the hive server).

## orchestrator.sh — lifecycle transition

This script orchestrates the CEO's initial build and the handoff to perpetual mode:

1. Validates manifest exists at `.company/manifest.json`.
2. Determines `FRESH` vs `RESUME` mode from manifest's `.mode` field.
3. Reads the CEO prompt from `$HIVE_ROOT/lib/agents/ceo.md`, appends assignment metadata (project root, brief path, manifest path, mode, auto flag).
4. Invokes `agent-spawn.sh` with the prompt piped via stdin.
5. On `INITIAL_BUILD_COMPLETE` signal: updates manifest to `perpetual` mode and `exec`s `company-start.sh`.
6. On failure: exits with path to CEO output log for debugging.

## init.sh — workspace bootstrap

Runs 9 sequential setup steps, each delegated to a script tool in `$HIVE_ROOT/lib/tools/`:

1. `init-workspace.sh` — creates `.company/` and `.hive/`
2. `scaffold-extract.sh` — extracts per-project scaffold
3. `init-manifest.sh` — writes manifest
4. `init-brief.sh` — writes project brief
5. `init-chat-channels.sh` — creates chat channels
6. `init-meeting-schedule.sh` — sets up meeting cadence
7. `init-marketing-scaffold.sh` — marketing deliverables
8. `init-legal-scaffold.sh` — privacy, ToS, license inventory
9. Git init + GitHub remote setup

Idempotent: if `.company/` already exists, only fills in missing pieces.

## PreToolUse hooks

Both `block-*.sh` scripts read PreToolUse JSON from stdin and exit with:
- **0** — allow the tool call to proceed
- **2** — block it (Claude sees the stderr message)

They are registered as hooks in `~/.claude/settings.json` with matchers `Write|Edit|NotebookEdit` (company-writes) and `Write|Edit|NotebookEdit|Bash` (company-writes) or just `Write|Edit` (admin-code-writes).

When modifying these hooks, test with a simulated PreToolUse payload:
```bash
echo '{"tool_name":"Write","tool_input":{"file_path":".company/departments/it/status.json"}}' \
  | bash block-company-writes.sh
# Expect: exit 2 with BLOCKED message on stderr
```

## See also

- Parent `CLAUDE.md` — Agent spawning overview, PreToolUse hooks
- `lib/tools/CLAUDE.md` — Agent spawning scripts, lifecycle orchestration
- `lib/agents/CLAUDE.md` — Agent prompt files loaded by these scripts
