#!/usr/bin/env bash
set -uo pipefail

# sprint-direction-write.sh — atomically write a sprint direction document.
# Called by the CEO during ceo-plan and ceo-bugfix-plan stages. The CEO
# pipes a JSON object on stdin; this script validates the schema, writes
# atomically, updates the sprint state's ceo_direction_path field.
#
# Usage:
#   echo '<json>' | sprint-direction-write.sh [--bugfix]
#
# Schema (ceo-plan, no --bugfix):
#   {
#     "rationale": "<2-4 sentences explaining sprint priorities>",
#     "departments": {
#       "backend-api-services": {
#         "direction": "<paragraph: what this dept should focus on this sprint>",
#         "seed_tickets": [               # optional; VPs may file more
#           {
#             "title": "...", "type": "feature", "priority": "high",
#             "description": "...",
#             "work_kind": "code|deliverable"   # optional (0.23) — the kind
#                                                  # of work the goal needs;
#                                                  # absent = the dept's kind
#                                                  # from the org chart
#           }
#         ]
#       },
#       ...
#     }
#   }
#
# Schema (--bugfix):
#   {
#     "rationale": "<why we're in bugfix mode + summary of the failure>",
#     "departments": {
#       "backend-api-services": {
#         "bugs": [
#           {"title": "...", "priority": "critical", "description": "..."}
#         ]
#       },
#       ...
#     }
#   }

BUGFIX=0
[ "${1:-}" = "--bugfix" ] && BUGFIX=1

SKILL_DIR="${SKILL_DIR:-${HIVE_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}}"
COMPANY_DIR="${COMPANY_DIR:-${PROJECT_ROOT:-$PWD}/.company}"
# shellcheck source=/dev/null
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/hive-state.sh"
export COMPANY_DIR

# shellcheck disable=SC1091
. "$SKILL_DIR/lib/tools/sprint-state.sh"
ss_init

SPRINT_ID=$(ss_get_field .sprint_id)
[ -z "$SPRINT_ID" ] && { echo "sprint-direction-write: no sprint_id in current.json" >&2; exit 1; }

if [ "$BUGFIX" = 1 ]; then
  OUT="$COMPANY_DIR/sprints/${SPRINT_ID}-bugfix-direction.json"
else
  OUT="$COMPANY_DIR/sprints/${SPRINT_ID}-direction.json"
fi

# Read JSON from stdin, validate (reads only), then one state-layer write.
INPUT=$(cat)

if ! printf '%s' "$INPUT" | jq empty >/dev/null 2>&1; then
  echo "sprint-direction-write: invalid JSON on stdin" >&2
  exit 2
fi

# Required keys
if ! printf '%s' "$INPUT" | jq -e '.rationale and .departments' >/dev/null 2>&1; then
  echo "sprint-direction-write: required keys missing (.rationale, .departments)" >&2
  exit 2
fi

if [ "$BUGFIX" = 0 ]; then
  # W5: seed_tickets[].work_kind is optional; when present it must be one
  # of code|deliverable (the same enum as the ticket field, WORK_KINDS in
  # lib/hive_state/core.py). A bad value is a usage error naming the field
  # — the CEO fixes the doc; the direction file is not written.
  BAD_KINDS=$(printf '%s' "$INPUT" | jq -r '[
    .departments[]?
    | (.seed_tickets // [])[]?
    | select(type == "object")
    | select(has("work_kind") and .work_kind != null)
    | select(.work_kind != "code" and .work_kind != "deliverable")
    | .work_kind
  ] | unique
  | map(if . == "" then "\"(empty string)\""
         elif type != "string" then (. | tostring)
         else . end)
  | join(", ")' 2>/dev/null)
  if [ -n "$BAD_KINDS" ]; then
    echo "sprint-direction-write: invalid work_kind in a seed ticket: $BAD_KINDS (allowed: code|deliverable)" >&2
    exit 2
  fi
fi

if [ "$BUGFIX" = 1 ]; then
  if ! printf '%s' "$INPUT" | jq -e '.departments | to_entries | all(.value.bugs and (.value.bugs | type == "array"))' >/dev/null 2>&1; then
    echo "sprint-direction-write: --bugfix requires .departments.<dept>.bugs to be an array" >&2
    exit 2
  fi
fi

REL="${OUT#"$COMPANY_DIR"/}"
printf '%s' "$INPUT" | hs --company-dir "$COMPANY_DIR" write "$REL" --stdin >/dev/null || {
  echo "sprint-direction-write: state-layer write failed for $REL" >&2
  exit 1
}

# Update sprint state
ss_set_ceo_direction_path "$OUT"

echo "sprint-direction-write: $OUT"
