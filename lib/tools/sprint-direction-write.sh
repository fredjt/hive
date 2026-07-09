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
#           {"title": "...", "type": "feature", "priority": "high", "description": "..."}
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

mkdir -p "$(dirname "$OUT")"

# Read JSON from stdin, validate, write atomically.
TMP=$(mktemp -t sprint-dir.XXXXXX.json)
trap 'rm -f "$TMP"' EXIT

cat > "$TMP"

if ! jq empty "$TMP" >/dev/null 2>&1; then
  echo "sprint-direction-write: invalid JSON on stdin" >&2
  exit 2
fi

# Required keys
if ! jq -e '.rationale and .departments' "$TMP" >/dev/null 2>&1; then
  echo "sprint-direction-write: required keys missing (.rationale, .departments)" >&2
  exit 2
fi

if [ "$BUGFIX" = 1 ]; then
  if ! jq -e '.departments | to_entries | all(.value.bugs and (.value.bugs | type == "array"))' "$TMP" >/dev/null 2>&1; then
    echo "sprint-direction-write: --bugfix requires .departments.<dept>.bugs to be an array" >&2
    exit 2
  fi
fi

mv "$TMP" "$OUT"
trap - EXIT

# Update sprint state
ss_set_ceo_direction_path "$OUT"

echo "sprint-direction-write: $OUT"
