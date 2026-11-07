#!/usr/bin/env bash
# pr-side-effects.sh — the gh-PR process edge for ticket moves.
#
# G1/G2 split: the ticket STATE mutation (file move, role/gate policy,
# done/in-progress field updates) lives in the state layer
# (lib/hive_state) and returns a pr_action decision. The PROCESS side
# effects — opening/merging/closing the PR via gh — live here, shared by
# ticket-move.sh and ticket-approve.sh so both paths behave identically.
#
#   source "$(dirname "$0")/pr-side-effects.sh"
#   hive_pr_side_effects <pr_action> <dept> <ticket-id> <reason> <comment> <author> <project-root>
#
# pr_action: none | open | merge | close
# Exit: 0 (side effects are best-effort; the move itself already committed).

hive_pr_side_effects() {
  local PR_ACTION="$1" DEPT="$2" TICKET_ID="$3" REASON="${4:-}" COMMENT="${5:-}" AUTHOR="${6:-ticket-move}" PROJ_ROOT="$7"
  local SCRIPT_DIR; SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  local PR_LOG_DIR="${COMPANY_DIR:?hive_pr_side_effects: COMPANY_DIR not set}/logs"
  mkdir -p "$PR_LOG_DIR"
  [ -n "$PROJ_ROOT" ] || PROJ_ROOT="$(cd "$COMPANY_DIR/.." 2>/dev/null && pwd)"

  case "$PR_ACTION" in
    none|"")
      return 0
      ;;
    open)
      local PR_OPEN_OUT PR_NUM
      PR_OPEN_OUT=""
      if [ -x "$SCRIPT_DIR/gh-pr-open.sh" ]; then
        PR_OPEN_OUT=$(bash "$SCRIPT_DIR/gh-pr-open.sh" "feature/$TICKET_ID" "$TICKET_ID" "$PROJ_ROOT" 2>&1 \
          | tee -a "$PR_LOG_DIR/gh-pr-open.log") || true
      fi
      PR_NUM=$(echo "$PR_OPEN_OUT" | grep -oE 'pr_num=[0-9]+|opened PR #[0-9]+|already open for [^ ]+' \
                | grep -oE '[0-9]+' | tail -1)
      if [ -n "$PR_NUM" ]; then
        echo "PR opened: #$PR_NUM"
      else
        echo "PR ready for review (no number)"
      fi
      ;;
    close)
      if [ -x "$SCRIPT_DIR/gh-pr-close.sh" ]; then
        HIVE_PR_OP_AUTHORIZED=1 bash "$SCRIPT_DIR/gh-pr-close.sh" \
          "feature/$TICKET_ID" "$TICKET_ID" "$PROJ_ROOT" --reason "${COMMENT:-Ticket rejected.}" \
          >> "$PR_LOG_DIR/gh-pr-close.log" 2>&1 || true
      fi
      ;;
    merge)
      if [ ! -x "$SCRIPT_DIR/gh-pr-merge.sh" ]; then
        return 0
      fi
      HIVE_PR_OP_AUTHORIZED=1 bash "$SCRIPT_DIR/gh-pr-merge.sh" \
        "feature/$TICKET_ID" "$PROJ_ROOT" \
        >> "$PR_LOG_DIR/gh-pr-merge.log" 2>&1
      local MERGE_RC=$?
      if [ "$MERGE_RC" = "0" ]; then
        bash "$SCRIPT_DIR/ticket-comment.sh" "$DEPT" "$TICKET_ID" "ticket-move" \
          "✅ PR merged on done — feature/${TICKET_ID} → main." 2>/dev/null || true
      elif [ "$MERGE_RC" = "5" ]; then
        # PR merge conflict: roll the ticket back through the state layer
        # (done -> in-progress, skip_tdd_red=true, verification cleared).
        echo "[ticket-move] PR merge conflict for $TICKET_ID; rolling back to in-progress with skip_tdd_red=true." >&2
        hs ticket-rollback-conflict --dept "$DEPT" --ticket "$TICKET_ID" >/dev/null || true
        echo "Rolled back $TICKET_ID: done -> in-progress (PR conflict)"
      else
        echo "[ticket-move] gh-pr-merge.sh exited $MERGE_RC for $TICKET_ID (see $PR_LOG_DIR/gh-pr-merge.log)" >&2
        local TAIL
        TAIL=$(tail -20 "$PR_LOG_DIR/gh-pr-merge.log" 2>/dev/null | tr '\n' ' ' | head -c 800)
        bash "$SCRIPT_DIR/ticket-comment.sh" "$DEPT" "$TICKET_ID" "ticket-move" \
          "⚠️ PR merge FAILED on done (gh-pr-merge.sh exit=$MERGE_RC) — ticket left in done; PR may still be open. Tail of merge log: ${TAIL}" \
          2>/dev/null || true
      fi
      ;;
  esac
  return 0
}
