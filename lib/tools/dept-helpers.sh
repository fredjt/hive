#!/usr/bin/env bash
# dept-helpers.sh — sourced helpers for distinguishing admin (planning-only)
# departments from engineering (code-producing) departments.
#
# Single source of truth so sprint-fill, verification-gates,
# and sprint-lifecycle all agree.
#
# Usage:
#   . "$SKILL_DIR/lib/tools/dept-helpers.sh"
#   if is_admin_dept "$dept"; then ... ; fi

# The fixed admin departments (planning-only roles). This is the single
# source of truth — the prompts (lib/agents/) describe the same rule.
DH_ADMIN_DEPTS=" hr it rd marketing legal finance executive "

is_admin_dept() {
  local d="${1:-}"
  case "$DH_ADMIN_DEPTS" in
    *" $d "*) return 0 ;;
    *)        return 1 ;;
  esac
}

# is_engineering_dept = !is_admin_dept (just for readability at call sites)
is_engineering_dept() {
  is_admin_dept "$1" && return 1 || return 0
}

# ── Work kind (0.23): the single lookup both the runner and the gates use ──
#
# work_kind_of <dept> <ticket-file-or-empty>
#
# Resolves the KIND of work a ticket is (code|deliverable) through the
# data, in priority order:
#   1. the ticket's own `work_kind` field (explicit wins — the goal's kind
#      is fixed at filing),
#   2. the org chart's dept `kind` (a 0.23+ chart declares the division of
#      labor per dept; an invalid/absent kind falls through — old charts
#      never fail the resolution),
#   3. the legacy name list: the fixed six were admin (deliverable-ish),
#      every dynamic dept was engineering (code) — today's semantics for
#      pre-0.23 charts and depts the chart doesn't know.
# Echoes `code` or `deliverable`.
work_kind_of() {
  local dept="${1:-}"
  local ticket_file="${2:-}"
  local k

  # 1. explicit ticket field
  if [ -n "$ticket_file" ] && [ -f "$ticket_file" ]; then
    k=$(jq -r '.work_kind // empty' "$ticket_file" 2>/dev/null)
    case "$k" in
      code|deliverable) echo "$k"; return 0 ;;
    esac
  fi

  # 2. org-chart dept kind
  local org=""
  if [ -n "${COMPANY_DIR:-}" ] && [ -f "${COMPANY_DIR}/org-chart.json" ]; then
    org="${COMPANY_DIR}/org-chart.json"
  elif [ -f ".company/org-chart.json" ]; then
    org=".company/org-chart.json"
  fi
  if [ -n "$org" ]; then
    k=$(jq -r --arg d "$dept" '.departments[$d].kind // empty' "$org" 2>/dev/null)
    case "$k" in
      code|deliverable) echo "$k"; return 0 ;;
    esac
  fi

  # 3. legacy name list (fallback ONLY)
  if is_admin_dept "$dept"; then
    echo "deliverable"
  else
    echo "code"
  fi
  return 0
}
