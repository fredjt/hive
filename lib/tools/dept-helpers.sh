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

# The fixed admin departments. Mirrors the list in
# lib/core/block-admin-code-writes.sh — keep in sync.
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
