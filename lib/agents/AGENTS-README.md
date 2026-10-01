# Agent Write Access Policy

## CRITICAL RULE: .company/ Directory is Read-Only via Write/Edit Tools

ALL agents must use scripts for ANY write operation to `.company/` directory.

## Allowed Write Operations

Agents CAN use Write/Edit tools for:
- Project source code (src/, backend/, frontend/, etc.)
- Project configuration files (package.json, tsconfig.json, etc.)
- Project documentation (README.md, docs/, etc.)
- Any file OUTSIDE `.company/` directory

## Forbidden Write Operations

Agents MUST NOT use Write/Edit tools for:
- `.company/` directory files (use scripts instead)

## Scripts for .company/ Operations

ALL .company/ writes MUST go through these scripts:

### Initialization (SKILL.md only)
- `bash SKILL_DIR/lib/tools/init-workspace.sh`
- `bash SKILL_DIR/lib/tools/init-manifest.sh`
- `bash SKILL_DIR/lib/tools/init-brief.sh`
- `bash SKILL_DIR/lib/tools/init-chat-channels.sh`
- `bash SKILL_DIR/lib/tools/init-meeting-schedule.sh`

### Status & Reporting
- `bash SKILL_DIR/lib/tools/status-update.sh <dept> --key value`
- `bash SKILL_DIR/lib/tools/report-write.sh <type> <filename> --stdin`
- `bash SKILL_DIR/lib/tools/research-findings.sh <ticket-id> --stdin`
- `bash SKILL_DIR/lib/tools/test-results.sh <dept> <component> --stdin`
- `bash SKILL_DIR/lib/tools/meeting-record.sh <type> <timestamp> --stdin`

### Communication
- `bash SKILL_DIR/lib/tools/inbox-write.sh <type> --content "..."`
- `bash SKILL_DIR/lib/tools/outbox-write.sh <type> --content "..."`
- `bash SKILL_DIR/lib/tools/chat-send.sh <channel> <author> <message>`

### Tickets (unchanged)
- All `ticket-*.sh` scripts
- All `sprint-*.sh` scripts

See `tools/company-api.md` for complete reference.

## Ticket Workflow

Tickets flow through these statuses: backlog → sprint-current → in-progress →
in-review → done. Agents interact with tickets via MCP tools (listed above)
and bash scripts. The sprint-lifecycle script manages transitions between
backlog, sprint-current, and in-progress states automatically.

## MCP Tools Reference

For ticket and status operations, prefer MCP tools over bash scripts:
- `mcp__hive__ticket_get(dept, ticket_id)` — read ticket JSON
- `mcp__hive__ticket_list(dept, status=None)` — summary list (paginates)
- `mcp__hive__ticket_create(dept, type, priority, title, description, ...)`
- `mcp__hive__ticket_comment(dept, ticket_id, author, comment)`
- `mcp__hive__ticket_approve(dept, ticket_id, verifier, rationale)` — verification IC only
- `mcp__hive__ticket_bounce(dept, ticket_id, verifier, rationale)` — verification IC only
- `mcp__hive__ticket_claim(dept, ticket_id, role, agent_id, agent_name)`
- `mcp__hive__chat_send(channel, author, department, message)`
- `mcp__hive__status_update(dept, key, value)`
- `mcp__hive__report_write(type, filename, content)`
- `mcp__hive__gates_check(dept, ticket_id)` — read gate report
- `mcp__hive__gates_run(dept, ticket_id)` — run gates synchronously
- `mcp__hive__inbox_write(type, dept, content)` — send to user inbox
