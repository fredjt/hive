# Hive MCP Tools

**49 tools registered.** All wrap bash scripts in lib/tools/.

### `agent_run_vp`
Run one cycle of the VP agent for a department.

Args:
  - `dept`: string (required)
  - `project_root`: string (required)

### `agent_spawn_ceo`
Spawn the CEO agent.

Args:
  - `project_root`: string (required)
  - `mode`: string

### `chat_read`
Read messages from a channel.

Args:
  - `channel`: string (required)
  - `last`: ?
  - `since`: ?
  - `company_dir`: ?

### `chat_send`
Post a message to a chat channel.

Args:
  - `channel`: string (required)
  - `author`: string (required)
  - `department`: string (required)
  - `message`: string (required)
  - `company_dir`: ?

### `classify_ticket`
Classify a free-text user request into (type, department) via small model.

Args:
  - `request`: string (required)
  - `company_dir`: ?

### `company_start`
Spawn all background daemons (VP loops, rebuilds, gates, dashboard).

Args:
  - `project_root`: string (required)

### `company_status`
Read processes.json for daemon status.

Args:
  - `project_root`: string (required)

### `company_stop`
Kill all recorded daemon PIDs.

Args:
  - `project_root`: string (required)

### `gates_check`
Read the most recent gate results for a single ticket.

Args:
  - `dept`: string (required)
  - `ticket_id`: string (required)
  - `company_dir`: ?

### `gates_run`
Run deterministic verification gates against in-review tickets in `dept`.

Args:
  - `dept`: string (required)
  - `project_root`: string (required)
  - `company_dir`: ?

### `inbox_process`
Process pending inbox entries (route to dept boards / expedited).

Args:
  - `company_dir`: ?
  - `dry_run`: boolean

### `inbox_write`
File a user request to the inbox.

Args:
  - `type`: string
  - `dept`: string
  - `content`: string
  - `company_dir`: ?

### `init_brief`
Write the project brief to `.company/brief.md`.

Args:
  - `brief`: string (required)
  - `clarifications`: ?
  - `company_dir`: ?

### `init_chat_channels`
Create chat channel files. Defaults to general/engineering/leadership/incidents.

Args:
  - `channels`: ?
  - `company_dir`: ?

### `init_manifest`
Create the initial `manifest.json` for a project.

Args:
  - `project_name`: string (required)
  - `auto_mode`: boolean
  - `mode`: string
  - `company_dir`: ?

### `init_meeting_schedule`
Create the empty `meetings/schedule.json`.

Args:
  - `company_dir`: ?

### `init_workspace`
Create a fresh `.company/` workspace in `project_root`.

Args:
  - `project_root`: string (required)

### `llm_tiny`
Run a prompt against the small CPU model (llm-tiny).

Args:
  - `prompt`: string (required)
  - `choices`: ?
  - `yesno`: boolean
  - `json_output`: boolean

### `meeting_record`
Record meeting notes.

Args:
  - `type`: string (required)
  - `timestamp`: string (required)
  - `content`: string (required)
  - `company_dir`: ?

### `narrate_status`
Turn a department's derived status.json into a one-sentence narrative.

Args:
  - `dept`: string (required)
  - `company_dir`: ?

### `outbox_archive`
Archive outbox entries older than `days` (preserves pending-approvals.md).

Args:
  - `days`: integer
  - `company_dir`: ?

### `outbox_write`
Write a CEO notification/approval/question to the outbox.

Args:
  - `type`: string (required)
  - `content`: string
  - `company_dir`: ?

### `prioritize_backlog`
Return the top N backlog tickets for a department, smart-prioritized.

Args:
  - `dept`: string (required)
  - `limit`: integer
  - `company_dir`: ?

### `report_write`
Write a report.

Args:
  - `type`: string (required)
  - `filename`: string (required)
  - `content`: string (required)
  - `company_dir`: ?

### `research_findings`
Write R&D research findings to .company/research/ongoing/<ticket-id>-findings.md.

Args:
  - `ticket_id`: string (required)
  - `content`: string (required)
  - `append`: boolean
  - `company_dir`: ?

### `roster_get`
Read the current derived roster (active VPs and ICs).

Args:
  - `active_only`: boolean
  - `dept`: ?
  - `company_dir`: ?

### `roster_rebuild`
Force a rebuild of roster.json from authoritative sources.

Args:
  - `company_dir`: ?

### `sprint_fill`
Promote backlog tickets into sprint-current to maintain `min_active` per dept.

Args:
  - `min_active`: integer
  - `dept`: ?
  - `company_dir`: ?

### `sprint_progress`
Show sprint ticket progression. With `archive_done=True`, also moves

Args:
  - `archive_done`: boolean
  - `company_dir`: ?

### `status_get`
Read the current derived status.

Args:
  - `dept`: ?
  - `company_dir`: ?

### `status_rebuild`
Force a rebuild of derived status.json files.

Args:
  - `dept`: ?
  - `company_dir`: ?

### `summarize_thread`
Condense recent activity in a chat channel into a 2-sentence digest.

Args:
  - `channel`: string (required)
  - `last`: integer
  - `company_dir`: ?

### `test_results`
Write IC-tester output for a component.

Args:
  - `dept`: string (required)
  - `component`: string (required)
  - `content`: string (required)
  - `format`: string
  - `company_dir`: ?

### `ticket_approve`
Verification IC's explicit approval. Required (with passing gates)

Args:
  - `dept`: string (required)
  - `ticket_id`: string (required)
  - `verifier`: string (required)
  - `rationale`: string (required)
  - `company_dir`: ?

### `ticket_claim`
Claim a ticket — sets assignee, links worktree, moves to in-progress.

Args:
  - `dept`: string (required)
  - `ticket_id`: string (required)
  - `role`: string (required)
  - `agent_id`: string (required)
  - `agent_name`: string (required)
  - `worktree_path`: ?
  - `company_dir`: ?

### `ticket_comment`
Add a comment to a ticket.

Args:
  - `dept`: string (required)
  - `ticket_id`: string (required)
  - `author`: string (required)
  - `comment`: string (required)
  - `company_dir`: ?

### `ticket_create`
Create a new ticket in the dept's backlog.

Args:
  - `dept`: string (required)
  - `type`: string (required)
  - `priority`: string (required)
  - `title`: string (required)
  - `description`: string (required)
  - `created_by`: string
  - `source`: string
  - `estimate`: ?
  - `company_dir`: ?

### `ticket_get`
Read a ticket's full JSON.

Args:
  - `dept`: string (required)
  - `ticket_id`: string (required)
  - `company_dir`: ?

### `ticket_list`
List tickets in a department. Optional status filter

Args:
  - `dept`: string (required)
  - `status`: ?
  - `company_dir`: ?

### `ticket_move`
Move a ticket between statuses.

Args:
  - `dept`: string (required)
  - `ticket_id`: string (required)
  - `new_status`: string (required)
  - `comment`: string (required)
  - `author`: string (required)
  - `reason`: ?
  - `force`: boolean
  - `company_dir`: ?

### `ticket_route_expedited`
Create an expedited ticket directly on the target dept's backlog,
bypassing the review queue. HIGH/CRITICAL blockers only.

Args:
  - `type`: string (required) — bug | infrastructure
  - `priority`: string (required) — high | critical
  - `title`: string (required)
  - `description`: string (required)
  - `created_by`: string (required)
  - `target_department`: string (required)
  - `estimate`: string
  - `company_dir`: ?

### `triage_test_failure`
Classify a test failure as flaky | real | unknown via small model.

Args:
  - `test_output`: string (required)

### `validate_workspace`
Validate `.company/` integrity in `project_root`. Returns gate-style report.

Args:
  - `project_root`: string (required)

### `worktree_auto_merge`
Find worktrees whose tickets are in done/ and merge them.

Args:
  - `project_root`: string (required)
  - `dry_run`: boolean

### `worktree_cleanup`
Remove stale worktrees (their branches were merged or deleted).

Args:
  - `project_root`: string (required)

### `worktree_create`
Create a git worktree for a ticket. Branch: `feature/<ticket-id>`.

Args:
  - `ticket_id`: string (required)
  - `project_root`: string (required)

### `worktree_list`
List all active worktrees with their branches.

Args:
  - `project_root`: string (required)

### `worktree_merge`
Merge a ticket's worktree branch into main and remove the worktree.

Args:
  - `ticket_id`: string (required)
  - `project_root`: string (required)

