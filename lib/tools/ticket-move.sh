#!/bin/bash
# Usage: ticket-move.sh <department> <ticket-id> <new-status> [completion-comment] [author] [reason]
# Example: ticket-move.sh backend-api-services TICK-API-0015 done "Implemented rate limiting middleware" "IC-Engineer" implemented
#
# When moving to done, a completion comment (arg 4) and reason (arg 6) are REQUIRED.
# Valid reasons: implemented, rejected, done
#   implemented - ticket was built and delivered
#   rejected    - ticket was reviewed and rejected (won't do)
#   done        - ticket was completed through non-code means (e.g. config change, manual fix)
# The script auto-detects the git branch and latest commit for the ticket.
# The ticket file is NEVER deleted - only moved.

COMPANY_DIR="${COMPANY_DIR:-.company}"
source "$(dirname "$0")/ticket-paths.sh"

DEPT="$1"
TICKET_ID="$2"
NEW_STATUS="$3"
COMMENT="$4"
AUTHOR="${5:-system}"
REASON="$6"

if [ -z "$DEPT" ] || [ -z "$TICKET_ID" ] || [ -z "$NEW_STATUS" ]; then
  echo "Usage: ticket-move.sh <department> <ticket-id> <new-status> [comment] [author] [reason]" >&2
  echo "Statuses: backlog, sprint-current, in-progress, in-review, done" >&2
  echo "When moving to done: comment, author, and reason are required." >&2
  echo "Valid reasons: implemented, rejected, done" >&2
  exit 1
fi

# Validate reason when moving to done
if [ "$NEW_STATUS" = "done" ]; then
  if [ -z "$REASON" ]; then
    echo "ERROR: $TICKET_ID — moving to done requires a reason (implemented | rejected | done)." >&2
    echo "" >&2
    echo "  If you are a verification IC approving this ticket, do NOT call ticket_move." >&2
    echo "  Use the atomic approval tool instead:" >&2
    echo "    mcp__hive__ticket_approve(dept=\"$DEPT\", ticket_id=\"$TICKET_ID\"," >&2
    echo "                              verifier=\"Verification-IC\"," >&2
    echo "                              rationale=\"<≥30 chars: which AC verified, edge cases probed>\")" >&2
    echo "" >&2
    echo "  If you are a VP rejecting (won't-do) during a VP stage, pass reason=\"rejected\":" >&2
    echo "    mcp__hive__ticket_move(dept=\"$DEPT\", ticket_id=\"$TICKET_ID\"," >&2
    echo "                           new_status=\"done\", reason=\"rejected\", ...)" >&2
    exit 1
  fi
  case "$REASON" in
    implemented|rejected|done) ;;
    *)
      echo "ERROR: Invalid reason '$REASON'. Must be one of: implemented, rejected, done" >&2
      exit 1
      ;;
  esac
fi

# ── ROLE + SCRIPTED-MOVE ENFORCEMENT ──
# Almost every ticket move is scripted by sprint-lifecycle /
# sprint-fill (which set HIVE_SCRIPTED_MOVE=1). The two
# agent-driven moves are: Verification-IC moving in-review→done (verified)
# or in-review→in-progress (bounce); and VP moving anywhere→done with
# reason=rejected (only during their own sprint stages).
#
# There is no --force escape hatch. A human who needs to manually override
# can `mv` the ticket file directly between status directories — this script
# only protects against agent misbehavior, not the operator.
HIVE_ROLE_ENV="${HIVE_ROLE:-}"
HIVE_SCRIPTED_MOVE_ENV="${HIVE_SCRIPTED_MOVE:-0}"
SKILL_DIR="${SKILL_DIR:-${HIVE_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}}"

# Find FROM status now so role check can use it.
TICKET_DIR_EARLY="$(ticket_dir "$DEPT")"
FROM_STATUS_EARLY=""
for st in backlog sprint-current in-progress in-review done archive; do
  if [ -f "$TICKET_DIR_EARLY/$st/$TICKET_ID.json" ]; then
    FROM_STATUS_EARLY="$st"
    break
  fi
done

deny_role() {
  echo "ERROR: ticket-move denied: HIVE_ROLE='$HIVE_ROLE_ENV' cannot move $TICKET_ID ($FROM_STATUS_EARLY → $NEW_STATUS, reason=${REASON:-none}). $1" >&2
  # Loudly redirect verification ICs to the right MCP tools — this is by
  # far the most common confused-caller case.
  if [ "$HIVE_ROLE_ENV" = "Verification-IC" ]; then
    echo "" >&2
    echo "  Verification ICs DO NOT use ticket_move directly. Use ONE of:" >&2
    echo "    To APPROVE (atomic — moves ticket to done + merges PR):" >&2
    echo "      mcp__hive__ticket_approve(dept=\"$DEPT\", ticket_id=\"$TICKET_ID\"," >&2
    echo "                                verifier=\"Verification-IC\", rationale=\"<≥30 chars>\")" >&2
    echo "    To REJECT (atomic — bounces ticket to in-progress with rationale):" >&2
    echo "      mcp__hive__ticket_bounce(dept=\"$DEPT\", ticket_id=\"$TICKET_ID\"," >&2
    echo "                               verifier=\"Verification-IC\", rationale=\"<≥80 chars>\")" >&2
    echo "    Both are ONE call. Do NOT also call ticket_move afterwards." >&2
  fi
  echo "  Operators: mv the ticket file by hand if you really need to override." >&2
  exit 3
}

if [ -n "$HIVE_ROLE_ENV" ]; then
  case "$NEW_STATUS:$REASON" in
    backlog:*)
      # Demoting back to backlog is the "defer" disposition: a sprint-stuck
      # ticket goes here so it's NOT done (verification IC's rejection still
      # stands) but the sprint can advance. Scripted only — agents must use
      # the normal forward path or VP rejection.
      [ "$HIVE_SCRIPTED_MOVE_ENV" = "1" ] || \
        deny_role "Demotion to backlog is scripted (sprint-lifecycle defer-on-bounce-limit)."
      ;;
    sprint-current:*)
      [ "$HIVE_SCRIPTED_MOVE_ENV" = "1" ] || \
        deny_role "Promotion to sprint-current is scripted (sprint-fill at stage 2.5/6c)."
      ;;
    in-progress:*)
      # Allowed callers:
      #   - SCRIPTED (sprint-lifecycle pre-spawn for tdd-red, bounce-back)
      #   - Verification-IC (bounce)
      if [ "$HIVE_SCRIPTED_MOVE_ENV" != "1" ] && [ "$HIVE_ROLE_ENV" != "Verification-IC" ]; then
        deny_role "Only the sprint-lifecycle script or Verification-IC can move to in-progress."
      fi
      ;;
    in-review:*)
      # Allowed callers:
      #   - SCRIPTED (sprint-lifecycle post-spawn for tdd-green exit-0)
      #   - IC (engineering, not tester) — though normally the script does it
      if [ "$HIVE_SCRIPTED_MOVE_ENV" != "1" ] && [ "$HIVE_ROLE_ENV" != "IC" ]; then
        deny_role "Only the sprint-lifecycle or engineering IC can move to in-review."
      fi
      ;;
    done:rejected)
      # Rejection paths:
      #   - HIVE_SCRIPTED_MOVE=1 (system safety valve: loop-cap, retry-cap)
      #   - HIVE_ROLE=Verification-IC (during verify stage — short-circuits the
      #     bounce-loop-until-cap waste when the ticket is structurally
      #     unworkable). The verifier should use ticket-reject.sh which
      #     enforces rationale length and writes a comment first.
      #   - HIVE_ROLE=VP (during their planning/review stages — strategic
      #     rejection of tickets that shouldn't be done at all).
      if [ "$HIVE_SCRIPTED_MOVE_ENV" = "1" ]; then
        :
      elif [ "$HIVE_ROLE_ENV" = "Verification-IC" ]; then
        :
      elif [ "$HIVE_ROLE_ENV" = "VP" ]; then
        if [ -f "$SKILL_DIR/lib/tools/sprint-state.sh" ]; then
          # shellcheck disable=SC1091
          . "$SKILL_DIR/lib/tools/sprint-state.sh"
          stg=$(ss_get_stage 2>/dev/null || echo "")
          case "$stg" in
            vp-plan|vp-review|ceo-bugfix-plan|vp-bugfix-plan) : ;;
            *) deny_role "VP rejection only allowed during stages vp-plan, vp-review, ceo-bugfix-plan, vp-bugfix-plan (current stage: ${stg:-unknown})." ;;
          esac
        fi
      else
        deny_role "Only Verification-ICs, VPs (during their stages), or the system can reject tickets."
      fi
      ;;
    done:*)
      # done with reason=implemented or done — verification IC only
      [ "$HIVE_ROLE_ENV" = "Verification-IC" ] || [ "$HIVE_SCRIPTED_MOVE_ENV" = "1" ] || \
        deny_role "Only Verification-IC can move from $FROM_STATUS_EARLY to done with reason=$REASON."
      ;;
  esac
fi

# ── HARD GATE: moving to done requires verification evidence ──
# Tickets can only reach done if BOTH:
#   1. verification-gates.sh has marked them gates_passed
#   2. A verification IC has explicitly approved (verified_by + verified_at fields)
#
# Exceptions (no gates required):
#   - REASON=rejected (VP or system rejection — won't-do, always allowed)
#   - HIVE_SCRIPTED_MOVE=1 (system safety valves: loop-cap, retry-cap)
#
# This prevents agents from prematurely shipping tickets and is the primary
# quality gate for the sprint lifecycle. Operators who need to override the gate
# should `mv` the ticket file by hand.
if [ "$NEW_STATUS" = "done" ] && [ "$REASON" != "rejected" ] && [ "$HIVE_SCRIPTED_MOVE_ENV" != "1" ]; then
  TICKET_FILE_FOR_GATE=""
  for st in in-review in-progress sprint-current backlog; do
    if [ -f "$(ticket_dir "$DEPT")/$st/$TICKET_ID.json" ]; then
      TICKET_FILE_FOR_GATE="$(ticket_dir "$DEPT")/$st/$TICKET_ID.json"
      break
    fi
  done

  if [ -z "$TICKET_FILE_FOR_GATE" ]; then
    echo "ERROR: ticket $TICKET_ID not found - cannot promote to done" >&2
    exit 1
  fi

  GATES_OK=$(jq -r '.gates_passed // false' "$TICKET_FILE_FOR_GATE" 2>/dev/null)
  VERIFIED_BY=$(jq -r '.verified_by // ""' "$TICKET_FILE_FOR_GATE" 2>/dev/null)
  VERIFIED_AT=$(jq -r '.verified_at // ""' "$TICKET_FILE_FOR_GATE" 2>/dev/null)

  if [ "$GATES_OK" != "true" ]; then
    echo "ERROR: ticket $TICKET_ID cannot move to done — deterministic gates have not passed." >&2
    echo "  The verification-gates daemon (90s loop) sets gates_passed once it" >&2
    echo "  has reviewed an in-review ticket. Wait for it, OR if you're a" >&2
    echo "  verification IC and want to ship NOW, just call ticket_approve" >&2
    echo "  (which is atomic — runs gates, stamps verification, moves to done):" >&2
    echo "    mcp__hive__ticket_approve(dept=\"$DEPT\", ticket_id=\"$TICKET_ID\"," >&2
    echo "                              verifier=\"Verification-IC\", rationale=\"<≥30 chars>\")" >&2
    exit 1
  fi
  if [ -z "$VERIFIED_BY" ] || [ -z "$VERIFIED_AT" ]; then
    echo "ERROR: ticket $TICKET_ID passed gates but has not been approved by a verification IC." >&2
    echo "  Verification ICs: do NOT call ticket_move directly. Use:" >&2
    echo "    mcp__hive__ticket_approve(dept=\"$DEPT\", ticket_id=\"$TICKET_ID\"," >&2
    echo "                              verifier=\"Verification-IC\", rationale=\"<≥30 chars>\")" >&2
    echo "  That call atomically stamps the verification fields, moves the" >&2
    echo "  ticket to done, and triggers the PR merge. ONE call, no follow-up." >&2
    exit 1
  fi
  echo "[ticket-move] Gate check passed for $TICKET_ID: gates=ok, verified_by=$VERIFIED_BY at $VERIFIED_AT" >&2
fi

TICKET_DIR="$(ticket_dir "$DEPT")"
VALID_STATUSES="backlog sprint-current in-progress in-review done"

if ! echo "$VALID_STATUSES" | grep -qw "$NEW_STATUS"; then
  echo "Invalid status: $NEW_STATUS. Must be one of: $VALID_STATUSES" >&2
  exit 1
fi

# Block moving to in-progress via ticket-move - use ticket-claim.sh instead.
# EXCEPTIONS:
#   - HIVE_SCRIPTED_MOVE=1 (sprint-lifecycle pre-spawn, bounce-back, verification-gates bounce)
#   - HIVE_ROLE=Verification-IC (bounce path; ticket-bounce.sh wraps this)
if [ "$NEW_STATUS" = "in-progress" ] \
   && [ "$HIVE_SCRIPTED_MOVE_ENV" != "1" ] \
   && [ "$HIVE_ROLE_ENV" != "Verification-IC" ]; then
  echo "ERROR: $TICKET_ID — agents cannot move tickets directly to in-progress." >&2
  echo "" >&2
  echo "  If you are a verification IC bouncing a rejected ticket, use:" >&2
  echo "    mcp__hive__ticket_bounce(dept=\"$DEPT\", ticket_id=\"$TICKET_ID\"," >&2
  echo "                             verifier=\"Verification-IC\"," >&2
  echo "                             rationale=\"<≥80 chars: numbered list of issues + file:line refs>\")" >&2
  echo "  That call is atomic (writes rationale + moves to in-progress + comments the GH issue)." >&2
  echo "" >&2
  echo "  Otherwise: in-progress moves are scripted (sprint-lifecycle pre-spawn for tdd-red," >&2
  echo "  sprint-lifecycle bounce-back after verify, verification-gates on gate-failure). Don't move it yourself." >&2
  exit 1
fi

# Find the ticket file in any status directory
FOUND=""
FOUND_STATUS=""
for status in $VALID_STATUSES; do
  if [ -f "$TICKET_DIR/$status/$TICKET_ID.json" ]; then
    FOUND="$TICKET_DIR/$status/$TICKET_ID.json"
    FOUND_STATUS="$status"
    break
  fi
done

if [ -z "$FOUND" ]; then
  echo "Ticket $TICKET_ID not found in department $DEPT" >&2
  exit 1
fi

if [ "$FOUND_STATUS" = "$NEW_STATUS" ]; then
  echo "Ticket $TICKET_ID is already in $NEW_STATUS"
  exit 0
fi

# Block moving tickets OUT of done or archive - these are terminal states
if [ "$FOUND_STATUS" = "done" ] || [ "$FOUND_STATUS" = "archive" ]; then
  echo "ERROR: Cannot move $TICKET_ID out of $FOUND_STATUS. Tickets in done/archive are final." >&2
  exit 1
fi

# Block moving to in-review unless the ticket has an assignee
if [ "$NEW_STATUS" = "in-review" ]; then
  HAS_ASSIGNEE=$(python3 -c "
import json
t=json.load(open('$FOUND'))
a=t.get('assignee',{})
name=a.get('name','') if isinstance(a,dict) else str(a) if a else ''
if name and name not in ('Unassigned','Unassigned - will be claimed by IC','unassigned',''):
    print('yes')
else:
    print('no')
" 2>/dev/null)
  if [ "$HAS_ASSIGNEE" != "yes" ] && [ "$HIVE_SCRIPTED_MOVE_ENV" != "1" ]; then
    echo "ERROR: $TICKET_ID — agents do not move tickets to in-review directly." >&2
    echo "" >&2
    echo "  Under the sprint-staged lifecycle, the sprint-lifecycle moves your ticket" >&2
    echo "  to in-review automatically when your turn exits 0. Just complete your" >&2
    echo "  work (commit + push) and exit cleanly — do NOT call ticket_move yourself." >&2
    echo "" >&2
    echo "  (Side note: this ticket also has no assignee field set, which would" >&2
    echo "  block the move regardless. If you really need a manual transition," >&2
    echo "  use mcp__hive__ticket_claim first.)" >&2
    exit 1
  fi
fi

# Ensure destination directory exists
mkdir -p "$TICKET_DIR/$NEW_STATUS"

TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
DEST="$TICKET_DIR/$NEW_STATUS/$TICKET_ID.json"

# Find git info for this ticket
PROJECT_ROOT=$(cd "$COMPANY_DIR/.." 2>/dev/null && pwd)
GIT_BRANCH=""
GIT_COMMIT=""
GIT_WORKTREE=""

if [ -n "$PROJECT_ROOT" ] && git -C "$PROJECT_ROOT" rev-parse --git-dir >/dev/null 2>&1; then
  # Check for a feature branch matching this ticket
  GIT_BRANCH=$(git -C "$PROJECT_ROOT" branch --list "feature/$TICKET_ID" 2>/dev/null | tr -d '* ')

  # Check for a worktree
  if [ -d "$PROJECT_ROOT/.worktrees/$TICKET_ID" ]; then
    GIT_WORKTREE="$PROJECT_ROOT/.worktrees/$TICKET_ID"
    # Get the latest commit from the worktree
    GIT_COMMIT=$(git -C "$GIT_WORKTREE" log --oneline -1 --format="%H" 2>/dev/null)
    GIT_BRANCH="feature/$TICKET_ID"
  fi

  # If no worktree, check for commits mentioning this ticket on any branch
  if [ -z "$GIT_COMMIT" ]; then
    GIT_COMMIT=$(git -C "$PROJECT_ROOT" log --all --oneline -1 --format="%H" --grep="$TICKET_ID" 2>/dev/null)
  fi

  # Get short hash
  GIT_COMMIT_SHORT=""
  if [ -n "$GIT_COMMIT" ]; then
    GIT_COMMIT_SHORT=$(echo "$GIT_COMMIT" | cut -c1-8)
  fi
fi

# Update the ticket JSON. CRITICAL: inputs come in via environment vars and
# the heredoc is single-quoted, so bash does NOT interpolate into python
# source. Any character (single quote, double quote, backtick, backslash,
# newline) in $COMMENT / $AUTHOR / paths is safe. The previous form
# interpolated `'''$COMMENT'''` inline — a trailing single quote in the
# comment produced four consecutive quotes ('''') which closes the triple-
# quoted literal early and trips a SyntaxError. With set -uo pipefail (no
# -e), the python failure was silent, and the unconditional `rm "$FOUND"`
# below then DELETED the ticket. Two layers of fix: env-var inputs prevent
# the crash; the post-write existence check prevents data loss if any
# future python regression manages to crash anyway.
TM_FOUND="$FOUND" \
TM_DEST="$DEST" \
TM_NEW_STATUS="$NEW_STATUS" \
TM_TIMESTAMP="$TIMESTAMP" \
TM_COMMENT="$COMMENT" \
TM_REASON="$REASON" \
TM_AUTHOR="$AUTHOR" \
TM_GIT_BRANCH="$GIT_BRANCH" \
TM_GIT_COMMIT_SHORT="$GIT_COMMIT_SHORT" \
TM_GIT_WORKTREE="$GIT_WORKTREE" \
TM_TICKET_ID="$TICKET_ID" \
TM_PROJECT_ROOT="$PROJECT_ROOT" \
python3 - <<'PYEOF'
import json, os
FOUND        = os.environ['TM_FOUND']
DEST         = os.environ['TM_DEST']
NEW_STATUS   = os.environ['TM_NEW_STATUS']
TIMESTAMP    = os.environ['TM_TIMESTAMP']
COMMENT      = os.environ.get('TM_COMMENT', '')
REASON       = os.environ.get('TM_REASON', '')
AUTHOR       = os.environ.get('TM_AUTHOR', '')
GIT_BRANCH   = os.environ.get('TM_GIT_BRANCH', '').strip()
GIT_COMMIT   = os.environ.get('TM_GIT_COMMIT_SHORT', '').strip()
GIT_WORKTREE = os.environ.get('TM_GIT_WORKTREE', '').strip()
TICKET_ID    = os.environ['TM_TICKET_ID']
PROJECT_ROOT = os.environ['TM_PROJECT_ROOT']

ticket = json.load(open(FOUND))
ticket['status'] = NEW_STATUS
ticket['updated_at'] = TIMESTAMP

if NEW_STATUS == 'done':
    # Prefix reflects the actual outcome: REJECTED for won't-do closures,
    # COMPLETED for everything else.
    comment_text = COMMENT if COMMENT else 'Marked as done'
    if 'comments' not in ticket or ticket['comments'] is None:
        ticket['comments'] = []
    prefix = 'REJECTED' if REASON == 'rejected' else 'COMPLETED'
    ticket['comments'].append({
        'author': AUTHOR,
        'timestamp': TIMESTAMP,
        'body': prefix + ': ' + comment_text,
    })

    # Update resolution with git info
    if not ticket.get('resolution') or ticket['resolution'] is None:
        ticket['resolution'] = {}
    ticket['resolution']['status']       = REASON
    ticket['resolution']['resolved_at']  = TIMESTAMP
    ticket['resolution']['resolved_by']  = AUTHOR
    if GIT_BRANCH:
        ticket['resolution']['git_branch'] = GIT_BRANCH
    if GIT_COMMIT:
        ticket['resolution']['commit']     = GIT_COMMIT
    if GIT_WORKTREE:
        ticket['resolution']['worktree']   = GIT_WORKTREE

elif NEW_STATUS == 'in-progress':
    # Link the feature branch + worktree paths onto the ticket as soon as
    # TDD-RED starts. The worktree may not physically exist YET (sprint-lifecycle
    # creates it right after this move), but the paths are deterministic and
    # the dashboard can already render the link.
    if not ticket.get('resolution') or ticket['resolution'] is None:
        ticket['resolution'] = {}
    ticket['resolution'].setdefault('status', None)
    ticket['resolution'].setdefault('resolved_at', None)
    ticket['resolution'].setdefault('resolved_by', None)
    ticket['resolution']['git_branch'] = 'feature/' + TICKET_ID
    ticket['resolution']['worktree']   = PROJECT_ROOT + '/.worktrees/' + TICKET_ID
    if not ticket['resolution'].get('started_at'):
        ticket['resolution']['started_at'] = TIMESTAMP
    if GIT_COMMIT:
        ticket['resolution']['commit'] = GIT_COMMIT

    if COMMENT:
        if 'comments' not in ticket or ticket['comments'] is None:
            ticket['comments'] = []
        ticket['comments'].append({
            'author': AUTHOR,
            'timestamp': TIMESTAMP,
            'body': COMMENT,
        })

# Merge comments from destination file if it exists (prevents comment loss
# from race conditions). Keep all unique comments by (timestamp, author,
# body[:100]).
if os.path.exists(DEST) and FOUND != DEST:
    try:
        existing = json.load(open(DEST))
        existing_comments = existing.get('comments') or []
        current_comments  = ticket.get('comments') or []
        seen, merged = set(), []
        for c in existing_comments + current_comments:
            key = (c.get('timestamp',''), c.get('author',''), (c.get('body','') or '')[:100])
            if key not in seen:
                seen.add(key)
                merged.append(c)
        merged.sort(key=lambda x: x.get('timestamp',''))
        ticket['comments'] = merged
    except Exception:
        pass

# Write atomically: tmp then rename, so a crash mid-write doesn't leave a
# truncated DEST that would then trigger the rm-FOUND below.
tmp = DEST + '.tmp.' + str(os.getpid())
with open(tmp, 'w') as fh:
    json.dump(ticket, fh, indent=2)
os.replace(tmp, DEST)
PYEOF
PYRC=$?

# Defense-in-depth: only delete the source if the destination actually exists
# and python exited cleanly. If python crashed for any reason, leave $FOUND
# in place so the ticket isn't lost — manual recovery is possible from the
# original location.
if [ "$PYRC" -ne 0 ] || [ ! -f "$DEST" ]; then
  echo "ERROR: ticket-move: python step failed (rc=$PYRC) or destination $DEST not written; LEAVING source $FOUND in place to prevent ticket loss." >&2
  exit 1
fi

# Remove from old location (only after new file is written, verified above).
if [ "$FOUND" != "$DEST" ]; then
  rm "$FOUND"
fi

# Clean up any duplicate copies in OTHER directories
for dup_status in $VALID_STATUSES; do
  dup_file="$TICKET_DIR/$dup_status/$TICKET_ID.json"
  if [ "$dup_status" != "$NEW_STATUS" ] && [ -f "$dup_file" ]; then
    rm "$dup_file"
  fi
done

PROJ_ROOT="${PROJECT_ROOT:-$(dirname "$COMPANY_DIR")}"
PR_LOG_DIR="$COMPANY_DIR/logs"
mkdir -p "$PR_LOG_DIR"

# GitHub issue integration removed — no longer mirrors ticket status to GH issues.

# ── PR + GH-issue side-effects (scripted; agents never touch either) ──
if [ "$NEW_STATUS" = "backlog" ]; then
  echo "Moved $TICKET_ID: $FOUND_STATUS -> backlog (deferred)"
  # Deferred-to-backlog: the ticket couldn't finish this sprint but is NOT
  # rejected. Verification IC's last rationale stands. PR (if any) is left
  # open — the same branch + PR get reused next sprint when sprint-fill
  # picks the ticket back up.

elif [ "$NEW_STATUS" = "in-progress" ] && [ "$FOUND_STATUS" = "sprint-current" ]; then
  echo "Moved $TICKET_ID: $FOUND_STATUS -> in-progress"
  # First time work starts on this ticket.

elif [ "$NEW_STATUS" = "in-progress" ] && [ "$FOUND_STATUS" = "in-review" ]; then
  echo "Moved $TICKET_ID: $FOUND_STATUS -> in-progress"
  # Bounce-back from verification (or crash recovery).

elif [ "$NEW_STATUS" = "in-review" ]; then
  echo "Moved $TICKET_ID: $FOUND_STATUS -> in-review"
  PR_OPEN_OUT=""
  if [ -x "$SKILL_DIR/lib/tools/gh-pr-open.sh" ]; then
    PR_OPEN_OUT=$(bash "$SKILL_DIR/lib/tools/gh-pr-open.sh" "feature/$TICKET_ID" "$TICKET_ID" "$PROJ_ROOT" 2>&1 \
      | tee -a "$PR_LOG_DIR/gh-pr-open.log") || true
  fi
  # gh-pr-open.sh prints the PR number on its last line if it created/found one.
  PR_NUM=$(echo "$PR_OPEN_OUT" | grep -oE 'pr_num=[0-9]+|opened PR #[0-9]+|already open for [^ ]+' \
            | grep -oE '[0-9]+' | tail -1)
  if [ -n "$PR_NUM" ]; then
    echo "PR opened: #$PR_NUM"
  else
    echo "PR ready for review (no number)";
  fi

elif [ "$NEW_STATUS" = "done" ]; then
  echo "Moved $TICKET_ID: $FOUND_STATUS -> done (branch: ${GIT_BRANCH:-none}, commit: ${GIT_COMMIT_SHORT:-none})"

  # PR side-effect: merge (verified) or close (rejected).
  if [ "$REASON" = "rejected" ]; then
    # Ticket rejected — close the PR.
    RATIONALE="${COMMENT:-Ticket rejected.}"
    if [ -x "$SKILL_DIR/lib/tools/gh-pr-close.sh" ]; then
      HIVE_PR_OP_AUTHORIZED=1 bash "$SKILL_DIR/lib/tools/gh-pr-close.sh" \
        "feature/$TICKET_ID" "$TICKET_ID" "$PROJ_ROOT" --reason "$RATIONALE" \
        >> "$PR_LOG_DIR/gh-pr-close.log" 2>&1 || true
    fi
  else
    # reason=implemented or reason=done → attempt merge. exit 5 == conflict.
    if [ -x "$SKILL_DIR/lib/tools/gh-pr-merge.sh" ]; then
      HIVE_PR_OP_AUTHORIZED=1 bash "$SKILL_DIR/lib/tools/gh-pr-merge.sh" \
        "feature/$TICKET_ID" "$PROJ_ROOT" \
        >> "$PR_LOG_DIR/gh-pr-merge.log" 2>&1
      MERGE_RC=$?
      if [ "$MERGE_RC" = "0" ]; then
        # Verified merge succeeded.
        # Visible "PR merged" comment on the ticket so the dashboard shows
        # outcome without operators having to dig in gh-pr-merge.log.
        bash "$SKILL_DIR/lib/tools/ticket-comment.sh" "$DEPT" "$TICKET_ID" "ticket-move" \
          "✅ PR merged on done — feature/${TICKET_ID} → main." 2>/dev/null || true
      elif [ "$MERGE_RC" = "5" ]; then
        # Notify the GH issue first (while still OPEN) before rolling back.
        # ── PR merge conflict rollback ──
        # Roll the ticket back so the engineering IC can rebase next loop.
        # This keeps the ticket in this sprint and the PR open.
        echo "[ticket-move] PR merge conflict for $TICKET_ID; rolling back to in-progress with skip_tdd_red=true." >&2
        ROLLBACK_DIR="$TICKET_DIR/in-progress"
        mkdir -p "$ROLLBACK_DIR"
        ROLLBACK_DEST="$ROLLBACK_DIR/$TICKET_ID.json"
        ROLLBACK_TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
        python3 - "$DEST" "$ROLLBACK_DEST" "$ROLLBACK_TS" <<'PYEOF'
import json, os, sys
src, dst, ts = sys.argv[1], sys.argv[2], sys.argv[3]
t = json.load(open(src))
t['status'] = 'in-progress'
t['updated_at'] = ts
t['skip_tdd_red'] = True
# Clear verification fields so the next loop's verify stage re-evaluates.
t['verified_by'] = ''
t['verified_at'] = ''
t['gates_passed'] = False
if 'comments' not in t or t['comments'] is None:
    t['comments'] = []
t['comments'].append({
    'author': 'ticket-move',
    'timestamp': ts,
    # Prefix "REJECTED by verification" matches what ticket-bounce.sh
    # writes — sprint-lifecycle's reconciliation uses this prefix
    # to tell "stuck-from-crash" tickets (no rejection comment) apart
    # from "needs rework after bounce" tickets (rejection comment exists).
    'body': 'REJECTED by verification: PR merge conflict on approve. Bounced to in-progress with skip_tdd_red=true. Engineering IC: rebase feature branch on main, resolve conflicts, force-push, then proceed normally. PR remains open.'
})
# Drop rejection/done resolution fields if they were set
if 'resolution' in t:
    t['resolution'] = None
json.dump(t, open(dst, 'w'), indent=2)
PYEOF
        rm -f "$DEST"
        # Don't close the PR — it stays open for the engineer's rebase.
        echo "Rolled back $TICKET_ID: done -> in-progress (PR conflict)"
        exit 0
      elif [ "$MERGE_RC" != "0" ]; then
        echo "[ticket-move] gh-pr-merge.sh exited $MERGE_RC for $TICKET_ID (see $PR_LOG_DIR/gh-pr-merge.log)" >&2
        # Non-conflict failure: leave ticket in done; the verification IC + log
        # capture is enough trail. (Alternative: roll back. We choose not to so
        # the verifier's approval is honored even if gh is having a bad day.)
        # Surface the failure as a ticket comment so the dashboard shows it
        # — operators have been missing silent merge failures.
        TAIL=$(tail -20 "$PR_LOG_DIR/gh-pr-merge.log" 2>/dev/null | tr '\n' ' ' | head -c 800)
        bash "$SKILL_DIR/lib/tools/ticket-comment.sh" "$DEPT" "$TICKET_ID" "ticket-move" \
          "⚠️ PR merge FAILED on done (gh-pr-merge.sh exit=$MERGE_RC) — ticket left in done; PR may still be open. Tail of merge log: ${TAIL}" \
          2>/dev/null || true
      fi
    fi
  fi

else
  echo "Moved $TICKET_ID: $FOUND_STATUS -> $NEW_STATUS"
fi
