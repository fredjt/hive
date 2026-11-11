# Hive MCP Tools

**~48 tools registered.** Most wrap bash scripts in `lib/tools/`; a few read `.company/` state directly. (The count drifts as tools are added; `lib/hive_mcp/tools/` is the truth.)

### `agent_run_ic`

Spawn an IC (Individual Contributor) agent for a single ticket.

Args:
  - `agent_name`: string (required)
  - `dept`: string (required)
  - `ticket_id`: string (required)
  - `project_root`: string (required)
  - `prompt_extra`: string

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
  - `last`: integer
  - `since`: string
  - `company_dir`: string

### `chat_send`

Post a message to a chat channel.

Args:
  - `channel`: string (required)
  - `author`: string (required)
  - `department`: string (required)
  - `message`: string (required)
  - `company_dir`: string

### `classify_ticket`

Classify a free-text user request into (type, department).

Args:
  - `request`: string (required)
  - `company_dir`: string

### `company_status`

Read processes.json for daemon status.

Args:
  - `project_root`: string (required)

### `gates_check`

Read the most recent gate results for a single ticket.

Args:
  - `dept`: string (required)
  - `ticket_id`: string (required)
  - `company_dir`: string

### `gates_run`

Run deterministic verification gates against in-review tickets in `dept`.

Args:
  - `dept`: string (required)
  - `project_root`: string
  - `ticket_id`: string
  - `company_dir`: string

### `gh_pr_merge`

Merge an open PR via gh (verification IC's GO path).

Args:
  - `branch`: string (required)
  - `project_root`: string (required)
  - `method`: string
  - `company_dir`: string

### `gh_pr_open`

Open a PR for a feature branch (idempotent).

Args:
  - `branch`: string (required)
  - `ticket_id`: string (required)
  - `project_root`: string (required)
  - `title`: string
  - `body`: string
  - `base`: string
  - `company_dir`: string

### `gh_status`

Report current GitHub integration health: gh installed?, authed?, remote URL, default branch.

Args:
  - `company_dir`: string

### `init_brief`

Write the project brief to `.company/brief.md`.

Args:
  - `brief`: string (required)
  - `clarifications`: string
  - `company_dir`: string

### `init_chat_channels`

Create chat channel files. Defaults to general/engineering/leadership/incidents.

Args:
  - `channels`: list of string
  - `company_dir`: string

### `init_manifest`

Create the initial `manifest.json` for a project.

Args:
  - `project_name`: string (required)
  - `auto_mode`: boolean
  - `mode`: string
  - `company_dir`: string

### `init_workspace`

Create a fresh `.company/` workspace in `project_root`.

Args:
  - `project_root`: string (required)

### `narrate_status`

Turn a department's derived status.json into a one-sentence narrative.

Args:
  - `dept`: string (required)
  - `company_dir`: string

### `prioritize_backlog`

Return the top N backlog tickets for a department, smart-prioritized.

Args:
  - `dept`: string (required)
  - `limit`: integer
  - `company_dir`: string

### `release_history`

List recent published releases from sprints/history/.

Args:
  - `limit`: integer
  - `company_dir`: string

### `release_status`

Read sprint state's release block, or a historical release file.

Args:
  - `version`: string
  - `company_dir`: string

### `release_verdict`

Write the release-ic GO/NO-GO verdict for the current sprint's release-verify stage. sprint-lifecycle reads the first VERDICT: line and routes the sprint to release-publish (GO) or ceo-bugfix-plan (NO-GO).

Args:
  - `verdict`: string (required)
  - `rationale`: string (required)
  - `blockers`: string
  - `company_dir`: string

### `report_write`

Write a report. Filename may include subdirectories (e.g. "2026-Q2/budget.md") — the parent directories are created automatically.

Args:
  - `type`: string (required)
  - `filename`: string (required)
  - `content`: string (required)
  - `company_dir`: string

### `research_findings`

Write R&D research findings to .company/research/ongoing/<ticket-id>-findings.md.

Args:
  - `ticket_id`: string (required)
  - `content`: string (required)
  - `append`: boolean
  - `company_dir`: string

### `roster_get`

Read the current derived roster (active VPs and ICs).

Args:
  - `active_only`: boolean
  - `dept`: string
  - `company_dir`: string

### `roster_rebuild`

Force a rebuild of roster.json from authoritative sources.

Args:
  - `company_dir`: string

### `sprint_fill`

Promote backlog tickets into sprint-current to maintain `min_active` per dept.

Args:
  - `min_active`: integer
  - `dept`: string
  - `company_dir`: string

### `sprint_progress`

Show sprint ticket progression. With `archive_done=True`, also moves done tickets into archive/.

Args:
  - `archive_done`: boolean
  - `company_dir`: string

### `status_get`

Read the current derived status.

Args:
  - `dept`: string
  - `company_dir`: string

### `status_rebuild`

Force a rebuild of derived status.json files.

Args:
  - `dept`: string
  - `company_dir`: string

### `summarize_thread`

Condense recent activity in a chat channel into a short summary.

Args:
  - `channel`: string (required)
  - `last`: integer
  - `company_dir`: string

### `test_results`

Write IC-tester output for a component.

Args:
  - `dept`: string (required)
  - `component`: string (required)
  - `content`: string (required)
  - `format`: string
  - `company_dir`: string

### `ticket_approve`

Verification IC's APPROVE path — atomic. Stamps the verification fields (verified_by, verified_at, verification_rationale) AND moves the ticket to done with reason=implemented in one call.

Args:
  - `dept`: string (required)
  - `ticket_id`: string (required)
  - `verifier`: string (required)
  - `rationale`: string (required)
  - `company_dir`: string

### `ticket_bounce`

Verification IC's BOUNCE path — return the ticket to the engineer for another loop iteration. Atomically: 1. Moves the ticket from in-review back to in-progress, writing the FULL rationale as the 'REJECTED by verification' comment (one comment per bounce — the move comment is canonical) 2. Posts a "bounced" comment on the linked GitHub issue (if any)

Args:
  - `dept`: string (required)
  - `ticket_id`: string (required)
  - `verifier`: string (required)
  - `rationale`: string (required)
  - `skip_tdd_red`: boolean
  - `company_dir`: string

### `ticket_claim`

Claim a ticket — sets assignee, links worktree, moves to in-progress.

Args:
  - `dept`: string (required)
  - `ticket_id`: string (required)
  - `role`: string (required)
  - `agent_id`: string (required)
  - `agent_name`: string (required)
  - `worktree_path`: string
  - `company_dir`: string

### `ticket_comment`

Add a comment to a ticket.

Args:
  - `dept`: string (required)
  - `ticket_id`: string (required)
  - `author`: string (required)
  - `comment`: string (required)
  - `company_dir`: string

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
  - `estimate`: string
  - `labels`: list of string
  - `acceptance_criteria`: list of string
  - `company_dir`: string

### `ticket_get`

Read a ticket's full JSON.

Args:
  - `dept`: string (required)
  - `ticket_id`: string (required)
  - `company_dir`: string

### `ticket_list`

List tickets, department-scoped or company-wide. Optional status filter (backlog, sprint-current, in-progress, in-review, done, archive) and priority filter. `dept` is optional (0.17): omitted, every department is listed, grouped — the result carries {"departments": {<dept>: doc}, "total": N, "mode": ...} instead of the per-dept doc {"department", "count", "tickets", "mode"}. A named department that does not exist is an error ("Department '<dept>' not found"); an existing one with zero tickets is a count-0 doc.

Args:
  - `dept`: string
  - `status`: string
  - `priority`: string
  - `company_dir`: string

### `ticket_move`

Move a ticket between statuses.

Args:
  - `dept`: string (required)
  - `ticket_id`: string (required)
  - `new_status`: string (required)
  - `comment`: string (required)
  - `author`: string (required)
  - `reason`: string
  - `company_dir`: string

### `ticket_reject`

Verification IC's REJECT path — close the ticket as won't-do without further loop iterations. Atomically: 1. Writes the rationale as a comment on the ticket 2. Moves the ticket from in-review to done with reason=rejected (auto-closes the linked PR)

Args:
  - `dept`: string (required)
  - `ticket_id`: string (required)
  - `verifier`: string (required)
  - `rationale`: string (required)
  - `company_dir`: string

### `ticket_route_expedited`

Create an expedited ticket directly on the target dept's backlog, bypassing the normal review queue. For HIGH/CRITICAL blockers only.

Args:
  - `type`: string (required)
  - `priority`: string (required)
  - `title`: string (required)
  - `description`: string (required)
  - `created_by`: string (required)
  - `target_department`: string (required)
  - `estimate`: string
  - `company_dir`: string

### `triage_test_failure`

Classify a test failure as flaky | real | setup | skip.

Args:
  - `test_name`: string (required)
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
