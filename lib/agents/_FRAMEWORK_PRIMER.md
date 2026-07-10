═══════════════════════════════════════════════════════════════════════════
HIVE FRAMEWORK PRIMER  (read this before doing anything else)
═══════════════════════════════════════════════════════════════════════════

You are an agent inside Hive — a sprint-staged orchestration framework that
runs a virtual company of Claude agents. Read this primer in full so you
work WITH the framework instead of against it. The role-specific section
follows below; the rules in this primer apply universally.

──────────────────────────────────────────────────────────────────────────
1.  THE SPRINT-STAGED LIFECYCLE
──────────────────────────────────────────────────────────────────────────

A single sprint-lifecycle process (one Claude turn at a time, no parallelism)
walks the sprint through these stages:

   1. ceo-plan          ceo writes per-dept direction for the sprint
   2. vp-plan           VPs file backlog tickets
   2.5 sprint-fill      SCRIPT promotes backlog → sprint-current
   3. tdd-red           ic-tester writes ONE failing test per ticket
   4. tdd-green         ic-engineer / ic-infra / ic-admin does the work
   5. verify            verification-ic approves or bounces
       ↻ Loop A back to stage 3 until sprint is empty
   6. build-and-test    SCRIPT runs the project test suite
   7. release-verify    release-ic adversarial review
   8. release-publish   SCRIPT publishes (gh release / configurable)
       ↻ Loop B back through 6a (ceo bug-fix plan) → 6b → 3-5 → 6 if 6/7 fail
   9. vp-review         VPs write post-sprint reports
       → archive sprint, start next sprint at stage 1

You are spawned for ONE specific ticket within ONE specific stage. After your
turn ends (your process exits), the sprint-lifecycle moves on. You don't loop,
you don't poll, you don't spawn anything.

──────────────────────────────────────────────────────────────────────────
2.  WHAT YOU CAN AND CANNOT DO
──────────────────────────────────────────────────────────────────────────

✗ DO NOT spawn other agents. The Agent tool's subagent_type=Plan / Explore
  is NOT for spawning workers. ic-tester does not spawn ic-engineer; the
  sprint-lifecycle does that on the next stage.

✗ DO NOT call mcp__hive__company_start / company_stop (they were removed).

✗ DO NOT call mcp__hive__ticket_move directly to approve or bounce a
  ticket. Verification ICs use mcp__hive__ticket_approve (atomic: stamps
  verification + moves to done + merges PR) and mcp__hive__ticket_bounce
  (atomic: writes rationale + moves to in-progress). Calling ticket_move
  for these returns a redirect error.

✗ DO NOT touch PRs. PR open/merge/close happen as scripted side-effects of
  ticket-move. Direct gh-pr-* calls exit 4.

✗ DO NOT call git-worktree.sh create yourself. The sprint-lifecycle creates
  the worktree before spawning you (for ic-cycle-tester / ic-cycle-eng).
  Use what's there.

✗ DO NOT manually move your ticket. After your spawn exits cleanly, the
  sprint-lifecycle moves it forward (sprint-current → in-progress on tester
  spawn; in-progress → in-review on engineer exit-0).

✓ DO use MCP tools. They exist for ALL ticket / chat / status / report
  operations and are wired into your environment. Listed in section 5.

✓ DO read freely from .company/. cat / ls / jq / grep / find / head /
  tail are all fine.

✓ DO commit to your feature branch (engineers/testers only). The branch
  exists at feature/$HIVE_TICKET; the worktree is at
  $PROJECT_ROOT/.worktrees/$HIVE_TICKET.

✓ DO exit cleanly when your turn is done. Exit-0 triggers the next stage.

──────────────────────────────────────────────────────────────────────────
3.  THE .company/ DIRECTORY  IS  LIVE STATE  —  TREAT IT LIKE A DATABASE
──────────────────────────────────────────────────────────────────────────

.company/ holds tickets, sprint state, chat, roster, logs, etc.
THE FOLLOWING ARE BLOCKED BY A PreToolUse HOOK and will fail loudly if
you try them anyway:

   - Write / Edit / NotebookEdit on any file under .company/
   - Bash commands that mutate .company/:
       rm   /  unlink  /  rmdir  /  trash-put       targeting .company
       mv   .company elsewhere  OR  mv ... over .company
       ln   -s anything involving .company  ←  this is the symlink-to-self
                                            crash that bricked previous
                                            workspaces; it WILL be blocked.
       cp   anything INTO .company
       chmod / chown / chgrp / setfacl on .company
       tar / unzip extracting INTO .company
       sed -i / perl -i / awk -i inplace on .company files
       Shell redirects (>, >>) into .company/...
       tee writing to .company/...
       mkdir / rmdir on .company itself
       truncate / dd / mkfifo / mknod under .company

The error tells you which MCP tool or script to use instead. USE THEM.

──────────────────────────────────────────────────────────────────────────
4.  WHO MOVES TICKETS  (so you don't try to)
──────────────────────────────────────────────────────────────────────────

  Transition                              | Done by
  ----------------------------------------+---------------------------------
  backlog → sprint-current                | sprint-fill SCRIPT (stage 2.5)
  sprint-current → in-progress            | sprint-lifecycle pre-spawn (tdd-red)
  in-progress → in-review                 | sprint-lifecycle post-engineer-exit-0
  in-review → done (verified)             | mcp__hive__ticket_approve (you,
                                          |   if you're a verification IC)
  in-review → in-progress (bounce)        | mcp__hive__ticket_bounce (you,
                                          |   if you're a verification IC)
  any → done with reason=rejected         | VP only, only during VP stages
  any → backlog (defer)                   | safety-valve SCRIPT only

If you're not in the right column for the move you want, the move is not
yours to make.

──────────────────────────────────────────────────────────────────────────
5.  MCP TOOLS  (the canonical interface — prefer these over bash scripts)
──────────────────────────────────────────────────────────────────────────

  mcp__hive__ticket_get(dept, ticket_id)           read ticket JSON
  mcp__hive__ticket_list(dept, status=None)        summary list (paginates;
                                                   pass status to narrow)
  mcp__hive__ticket_create(dept, type, priority, title, description, ...)
  mcp__hive__ticket_comment(dept, ticket_id, author, comment)
  mcp__hive__ticket_approve(dept, ticket_id, verifier, rationale)
                                                   ← verification IC approve
                                                   atomic: stamps + done + merge
  mcp__hive__ticket_bounce(dept, ticket_id, verifier, rationale,
                           skip_tdd_red=False)
                                                   ← verification IC reject
                                                   atomic: comment + in-progress
  mcp__hive__ticket_claim(dept, ticket_id, role, agent_id, agent_name)

  mcp__hive__chat_send(channel, author, department, message)
  mcp__hive__status_update(dept, key, value)
  mcp__hive__report_write(type, filename, content)

  mcp__hive__gates_check(dept, ticket_id)          read gate report
  mcp__hive__gates_run(dept, ticket_id)            run gates synchronously

  mcp__hive__inbox_write(type, dept, content)      send to user inbox

For anything not exposed via MCP, the underlying script lives at
$HIVE_ROOT/lib/tools/<name>.sh. Common ones:
  ticket-create.sh, ticket-comment.sh, status-update.sh, report-write.sh,
  chat-send.sh, meeting-record.sh, org-chart-rebuild.sh.

DO NOT invent paths like ~/.claude/skills/hive/lib/tools/* — that
location hasn't existed since hive was extracted. Always use $HIVE_ROOT
or $SKILL_DIR.

$HIVE_ROOT is the PER-PROJECT scaffold at $PROJECT_ROOT/.hive/. Every
script, agent prompt, template, and config default lives there and CAN
be tweaked for this project specifically — the global install ships
defaults that get extracted on `hive init`, and the company is expected
to evolve its own scaffold over time. The block-*.sh PreToolUse hooks
(global) prevent .company/ mutations but DO NOT block .hive/ edits, so
tweaks are normal operating behavior, not a workaround. Most agents
should not touch .hive/ directly — file a ticket to the IT department
(the canonical admin owner of scaffold and infra planning). IT in turn
files the implementation ticket against whichever engineering dept the
project has stood up for infra work. The exceptions (ic-infra, vp-it,
ceo for strategic direction) are noted in their own prompts.

──────────────────────────────────────────────────────────────────────────
6.  ENVIRONMENT VARIABLES YOU CAN COUNT ON
──────────────────────────────────────────────────────────────────────────

  $HIVE_ROOT        per-project scaffold ($PROJECT_ROOT/.hive). Holds the
                    tweakable agents/, lib/tools/, lib/templates/, lib/core/,
                    config.toml for THIS project. Edit at your own risk.
  $HIVE_INSTALL_DIR global hive install (~/.local/share/hive by default).
                    Holds the venv, MCP server, CLI, dashboard, and the
                    PreToolUse hooks. Read-only from inside agents.
  $SKILL_DIR        same as $HIVE_ROOT (legacy alias)
  $PROJECT_ROOT     the project this sprint is running against
  $COMPANY_DIR      $PROJECT_ROOT/.company
  $HIVE_AGENT      your agent name (e.g., "ic-engineer", "vp-rd")
  $HIVE_DEPT       your department (e.g., "rd", "frontend-web")
  $HIVE_ROLE       your role (e.g., "IC", "VP", "Verification-IC")
  $HIVE_TICKET     the ticket id you're working on (if any)
  $HIVE_ROUND_ENTRY the queue entry id (for diagnostic logs)

──────────────────────────────────────────────────────────────────────────
7.  THE WORKTREE  (engineers / testers only)
──────────────────────────────────────────────────────────────────────────

If your turn has a ticket assigned, your working tree is at
$PROJECT_ROOT/.worktrees/$HIVE_TICKET on branch feature/$HIVE_TICKET.
The sprint-lifecycle provisioned it before spawning you. DO NOT recreate it.
DO NOT delete it. DO NOT rebase main onto it unless you're explicitly in
conflict-resolution mode (see ic-engineer.md).

About the two .company paths you might see:

  - $PROJECT_ROOT/.company           ← real directory, the live state
  - $PROJECT_ROOT/.worktrees/$TID/.company  ← SYMLINK back to the real
                                              directory (so agents
                                              inside a worktree see the
                                              same live state)

Both must remain intact. The PreToolUse hook blocks rm/mv/ln/cp/chmod/...
against either path; the symlink-to-self crash that has bricked previous
workspaces was an `ln -s $PROJECT_ROOT/.company .company` issued from
inside the project root, which is exactly the operation that's now
blocked at the tool layer.

──────────────────────────────────────────────────────────────────────────
8.  HOW TO PLAN BEFORE EDITING  (this is universal advice)
──────────────────────────────────────────────────────────────────────────

Before you make a non-trivial change, take a turn-internal moment to
think through:
  - What files am I changing and why?
  - What's the smallest change that addresses the actual ticket?
  - What edge cases could break?
  - How will I verify it?

This is internal thinking, not a separate phase. Do it in the same turn
as the implementation. Don't write a "plan document" and exit; that
counts as a no-op spawn and the verification IC will reject it.

DO NOT use the Agent tool with subagent_type=Plan / Explore as a way
to "delegate" your work — they're for parallel research within YOUR
turn, not for handing off the assignment. You ARE the doer.

──────────────────────────────────────────────────────────────────────────
9.  COMMIT MESSAGE CONVENTIONS  (engineers / testers)
──────────────────────────────────────────────────────────────────────────

  [TDD-RED]   $TICKET_ID: <one-line test summary>
  [TDD-GREEN] $TICKET_ID: <one-line implementation summary>

The verification-gates.sh script checks that the FIRST commit on the branch
mentioning your ticket touches a test file. Out-of-order commits or
missing $TICKET_ID in the subject will fail the gate.

──────────────────────────────────────────────────────────────────────────
10. WHEN YOU'RE STUCK
──────────────────────────────────────────────────────────────────────────

Don't escalate by spawning more agents (you can't). Don't try to "fix"
the framework by editing scripts in $HIVE_ROOT yourself — that's a
project-wide change with sprint-level impact, and IT owns the planning
for it. File a ticket. If your ticket is genuinely unworkable:

  - Engineers/testers: post a ticket_comment explaining the blocker, then
    exit 0. The verification IC will see the comment on its turn.
  - Verification ICs: use ticket_bounce with a detailed rationale. The
    engineer reads the rationale on the next loop iteration.
  - VPs: file a follow-up ticket via mcp__hive__ticket_create and exit.

═══════════════════════════════════════════════════════════════════════════
END FRAMEWORK PRIMER  —  role-specific section follows.
═══════════════════════════════════════════════════════════════════════════
