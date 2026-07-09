#!/usr/bin/env bash
set -uo pipefail

# gh-pr-open.sh — open a PR for a feature branch (idempotent).
# Used by worktree-merger when a ticket transitions to in-review.
#
# Usage: gh-pr-open.sh <branch> <ticket-id> [project-root]
#                     [--title TITLE] [--body BODY] [--base BRANCH]

BRANCH="${1:-}"
TICKET_ID="${2:-}"
PROJECT_ROOT="${3:-${PROJECT_ROOT:-$PWD}}"
shift 3 2>/dev/null || true

if [ -z "$BRANCH" ] || [ -z "$TICKET_ID" ]; then
  echo "Usage: gh-pr-open.sh <branch> <ticket-id> [project-root] [--title T] [--body B] [--base BRANCH]" >&2
  exit 2
fi

PROJECT_ROOT="$(cd "$PROJECT_ROOT" 2>/dev/null && pwd)" || { echo "gh-pr-open: bad project root" >&2; exit 2; }
SKILL_DIR="${HIVE_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}"

TITLE=""
BODY=""
BASE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --title) TITLE="$2"; shift 2 ;;
    --body)  BODY="$2";  shift 2 ;;
    --base)  BASE="$2";  shift 2 ;;
    *) shift ;;
  esac
done

. "$SKILL_DIR/lib/tools/gh-helpers.sh"

if ! gh_preflight 2>/tmp/gh-pr-open.preflight; then
  cat /tmp/gh-pr-open.preflight >&2
  exit 0   # soft no-op so worktree-merger can keep running
fi

[ -z "$BASE" ] && BASE=$(gh_default_branch)
[ -z "$BASE" ] && BASE="main"

# Check if a PR is already open for this branch — idempotent.
existing=$(gh_pr_for_branch "$BRANCH")
if [ -n "$existing" ]; then
  echo "gh-pr-open: PR #$existing already open for $BRANCH"
  echo "$existing"
  exit 0
fi

# Default title/body. Also pick up external_id (gh-issue-N) so the PR body
# can use GitHub's `Closes #N` syntax — that's what makes GitHub auto-close
# the linked issue when the PR merges, without any extra script call.
COMPANY_DIR="${COMPANY_DIR:-$PROJECT_ROOT/.company}"
ticket_title=""
ticket_desc=""
ticket_external=""
for st in in-review in-progress sprint-current backlog; do
  for dept_dir in "$COMPANY_DIR"/departments/*/; do
    f="$dept_dir/tickets/$st/${TICKET_ID}.json"
    [ -f "$f" ] || continue
    ticket_title=$(jq -r '.title // ""' "$f" 2>/dev/null)
    ticket_desc=$(jq -r  '.description // ""' "$f" 2>/dev/null)
    ticket_external=$(jq -r '.external_id // ""' "$f" 2>/dev/null)
    break 2
  done
done

# Build the "closes" line if external_id matches gh-issue-N. Multiple
# closes-keyword keywords work; "Closes" is GitHub's canonical.
CLOSES_LINE=""
if [ -n "$ticket_external" ] && [ "${ticket_external#gh-issue-}" != "$ticket_external" ]; then
  ISSUE_NUM_FOR_CLOSE="${ticket_external#gh-issue-}"
  CLOSES_LINE="Closes #${ISSUE_NUM_FOR_CLOSE}

"
fi

[ -z "$TITLE" ] && TITLE="${TICKET_ID}: ${ticket_title:-implementation}"
if [ -z "$BODY" ]; then
  BODY="${CLOSES_LINE}Hive ticket: ${TICKET_ID}

${ticket_desc:-}

---
*This PR was auto-opened by hive when the ticket transitioned to in-review. The verification IC will review and merge or reject.*"
fi

cd "$PROJECT_ROOT"

# Autocommit + push: ICs sometimes leave uncommitted work in the worktree
# when they call ticket-move.sh in-review. Without this step, gh pr create
# either fails (if branch isn't on remote) or opens a PR missing the latest
# work. Both fixed here.
WT_PATH=""
if [ -d "$PROJECT_ROOT/.worktrees/$TICKET_ID" ]; then
  WT_PATH="$PROJECT_ROOT/.worktrees/$TICKET_ID"
elif git -C "$PROJECT_ROOT" worktree list --porcelain 2>/dev/null \
       | awk -v b="refs/heads/$BRANCH" '/^worktree /{p=$2} /^branch /{if($2==b) print p}' \
       | head -1 >/tmp/gh-pr-open.wt.$$; then
  WT_PATH=$(cat /tmp/gh-pr-open.wt.$$)
  rm -f /tmp/gh-pr-open.wt.$$
fi

if [ -n "$WT_PATH" ] && [ -d "$WT_PATH" ]; then
  if [ -n "$(git -C "$WT_PATH" status --porcelain 2>/dev/null)" ]; then
    echo "gh-pr-open: autocommitting uncommitted changes in $WT_PATH"
    # Stage everything EXCEPT .company and .worktrees. Both are agent
    # state, not project source. The .company symlink in particular MUST
    # NOT be committed: each worktree's .company is a symlink back to the
    # project root's .company, and committing it propagates a self-loop
    # via git checkout into every clone of main, breaking the workspace
    # on every git operation forever. This was the root cause of the
    # recurring .company-symlink-deletion crash on photonic-proc.
    git -C "$WT_PATH" add -A -- ':!.company' ':!.company/**' ':!.worktrees' ':!.worktrees/**' 2>/dev/null || true
    # Commit message is intentionally [TDD-GREEN]-prefixed when on a feature
    # branch so the tdd-order gate keeps working — autocommitted leftovers
    # are by definition not the FIRST commit on the branch (the IC has
    # presumably committed at least once via TDD-RED + initial GREEN before
    # the move-to-in-review). If this is the first commit it will fail the
    # tdd-order gate, which is correct behavior.
    git -C "$WT_PATH" -c user.email=hive@local -c user.name=hive \
      commit -m "[TDD-GREEN] $TICKET_ID: autocommit on in-review transition" \
      >/dev/null 2>&1 || true
  fi
  # Push branch so remote has the latest tip before gh pr create.
  if [ -x "$SKILL_DIR/lib/tools/gh-push-branch.sh" ]; then
    bash "$SKILL_DIR/lib/tools/gh-push-branch.sh" "$BRANCH" "$PROJECT_ROOT" >/dev/null 2>&1 || true
  else
    git -C "$WT_PATH" push -u origin "$BRANCH" >/dev/null 2>&1 || true
  fi
fi

echo "gh-pr-open: opening PR for $BRANCH → $BASE (ticket=$TICKET_ID)"
out=$(gh pr create --head "$BRANCH" --base "$BASE" --title "$TITLE" --body "$BODY" 2>&1) || {
  echo "gh-pr-open: gh pr create failed: $out" >&2
  exit 1
}
# extract the PR number from the URL gh prints
pr_num=$(echo "$out" | grep -oE '/pull/[0-9]+' | head -1 | cut -d/ -f3)
echo "gh-pr-open: opened PR #${pr_num:-?} ($out)"
[ -n "$pr_num" ] && echo "$pr_num"

# Record in the integrations sidecar
MIRROR="$COMPANY_DIR/integrations/github-mirror.json"
mkdir -p "$(dirname "$MIRROR")"
[ -f "$MIRROR" ] || echo '{"issues":{},"prs":{}}' > "$MIRROR"
NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)
TMP="$MIRROR.tmp.$$"
PR_NUM="$pr_num" TICKET_ID="$TICKET_ID" BRANCH="$BRANCH" NOW="$NOW" MIRROR="$MIRROR" TMP="$TMP" python3 - <<'PYEOF'
import json, os
m = json.load(open(os.environ["MIRROR"]))
m.setdefault("prs", {})[os.environ["PR_NUM"]] = {
  "ticket_id":  os.environ["TICKET_ID"],
  "branch":     os.environ["BRANCH"],
  "opened_at":  os.environ["NOW"],
  "merged_at":  None,
  "merged_into": None,
}
json.dump(m, open(os.environ["TMP"], "w"), indent=2)
os.replace(os.environ["TMP"], os.environ["MIRROR"])
PYEOF
