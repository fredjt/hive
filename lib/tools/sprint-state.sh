#!/usr/bin/env bash
# sprint-state.sh — sourced helpers for reading/writing .company/sprints/current.json.
#
# The sprint object is the first-class lifecycle state. Stages are explicit
# and the sprint-lifecycle script dispatches on `.stage`.
#
# Usage (in another script):
#   . "$SKILL_DIR/lib/tools/sprint-state.sh"
#   ss_init "$COMPANY_DIR"          # ensures sprints/current.json exists
#   stage=$(ss_get_stage)           # current stage name
#   ss_set_stage tdd-red            # transition; updates stage_started_at
#   n=$(ss_active_ticket_count)     # tickets in sprint-current+in-progress+in-review
#   ss_archive_and_start_new        # called at END
#
# Single source of truth for stage names — kept in sync with sprint-lifecycle.sh.

# All operations are flock'd on the file itself so concurrent reads don't tear writes.

SS_VALID_STAGES=(
  ceo-plan
  vp-plan
  sprint-fill
  tdd-red
  tdd-green
  verify
  build-and-test
  release-verify
  release-publish
  ceo-bugfix-plan
  vp-bugfix-plan
  vp-review
  complete
)

ss_state_file() {
  echo "${COMPANY_DIR:?COMPANY_DIR not set}/sprints/current.json"
}

ss_state_dir() {
  echo "${COMPANY_DIR:?COMPANY_DIR not set}/sprints"
}

ss_now() { date -u +%Y-%m-%dT%H:%M:%SZ; }

ss__lock_fd() {
  local lock="$(ss_state_dir)/.lock"
  mkdir -p "$(ss_state_dir)"
  touch "$lock"
  echo "$lock"
}

ss_init() {
  local dir; dir=$(ss_state_dir)
  local f;   f=$(ss_state_file)
  mkdir -p "$dir/history"
  [ -f "$f" ] && return 0
  local sid="sprint-$(date -u +%Y%m%dT%H%M%SZ)"
  local now;  now=$(ss_now)
  cat > "$f" <<JSON
{
  "sprint_id": "$sid",
  "started_at": "$now",
  "stage": "ceo-plan",
  "stage_started_at": "$now",
  "loop_iteration": 0,
  "release_retry_count": 0,
  "tickets_in_sprint": [],
  "ceo_direction_path": null,
  "release": {
    "version": null,
    "test_log": null,
    "verify_log": null,
    "tarball": null,
    "sha256": null,
    "publish_status": "pending"
  }
}
JSON
}

ss_get_stage() {
  local f; f=$(ss_state_file)
  [ -f "$f" ] || { echo "ceo-plan"; return 0; }
  jq -r '.stage // "ceo-plan"' "$f"
}

ss_get_field() {
  local f; f=$(ss_state_file)
  [ -f "$f" ] || { echo ""; return 0; }
  jq -r "$1 // empty" "$f"
}

ss__atomic_jq() {
  local prog="$1"; shift
  local f; f=$(ss_state_file)
  local lock; lock=$(ss__lock_fd)
  local tmp="${f}.tmp.$$"
  (
    flock -x 9
    jq "$@" "$prog" "$f" > "$tmp" && mv "$tmp" "$f"
  ) 9>"$lock"
}

ss_set_stage() {
  local new="$1"
  local valid=0
  for s in "${SS_VALID_STAGES[@]}"; do
    [ "$s" = "$new" ] && valid=1 && break
  done
  if [ "$valid" = 0 ]; then
    echo "sprint-state: invalid stage '$new'" >&2
    return 2
  fi
  ss__atomic_jq '.stage = $s | .stage_started_at = $t' \
    --arg s "$new" --arg t "$(ss_now)"
}

ss_inc_loop_iteration() {
  ss__atomic_jq '.loop_iteration = (.loop_iteration + 1)'
}

ss_reset_loop_iteration() {
  ss__atomic_jq '.loop_iteration = 0'
}

ss_inc_release_retry() {
  ss__atomic_jq '.release_retry_count = (.release_retry_count + 1)'
}

ss_set_release_field() {
  local key="$1" val="$2"
  ss__atomic_jq '.release[$k] = $v' --arg k "$key" --arg v "$val"
}

ss_set_ceo_direction_path() {
  ss__atomic_jq '.ceo_direction_path = $p' --arg p "$1"
}

ss_set_tickets_in_sprint() {
  ss__atomic_jq '.tickets_in_sprint = $a' --argjson a "$1"
}

ss_mark_enqueued() {
  ss__atomic_jq '.stage_enqueued = true'
}

ss_is_enqueued() {
  local val
  val=$(jq -r .stage_enqueued "$(ss_state_file)" 2>/dev/null || echo false)
  [ "$val" = "true" ]
}

ss_active_ticket_count() {
  local depts_dir="${COMPANY_DIR:?COMPANY_DIR not set}/departments"
  [ -d "$depts_dir" ] || { echo 0; return 0; }
  local n=0
  while IFS= read -r f; do
    [ -z "$f" ] && continue
    n=$((n + 1))
  done < <(find "$depts_dir"/*/tickets/{sprint-current,in-progress,in-review} -maxdepth 1 -name '*.json' -type f 2>/dev/null)
  echo "$n"
}

ss_unverified_in_review_tickets() {
  local depts_dir="${COMPANY_DIR:?COMPANY_DIR not set}/departments"
  [ -d "$depts_dir" ] || return 0
  while IFS= read -r f; do
    [ -z "$f" ] && continue
    local v; v=$(jq -r '.verified_by // empty' "$f" 2>/dev/null)
    if [ -z "$v" ]; then
      local tid;  tid=$(basename "$f" .json)
      local dept; dept=$(echo "$f" | sed -n 's|.*/departments/\([^/]*\)/tickets/.*|\1|p')
      echo "$dept $tid"
    fi
  done < <(find "$depts_dir"/*/tickets/in-review -maxdepth 1 -name '*.json' -type f 2>/dev/null)
}

ss_archive_and_start_new() {
  local f; f=$(ss_state_file)
  [ -f "$f" ] || { ss_init; return 0; }
  ss__atomic_jq '.stage = "complete" | .ended_at = $t' --arg t "$(ss_now)"
  local sid; sid=$(jq -r '.sprint_id' "$f")

  local skill_dir="${SKILL_DIR:-${HIVE_ROOT:-}}"
  local proj_root="${PROJECT_ROOT:-${COMPANY_DIR%/.company}}"
  if [ -n "$skill_dir" ] && [ -x "$skill_dir/lib/tools/sprint-archive-done.sh" ] \
     && [ -n "$proj_root" ]; then
    bash "$skill_dir/lib/tools/sprint-archive-done.sh" "$proj_root" "$sid" \
      >> "${COMPANY_DIR}/logs/sprint-lifecycle.log" 2>&1 \
      || echo "ss_archive_and_start_new: sprint-archive-done.sh failed" >&2
  fi

  local lock; lock=$(ss__lock_fd)
  (
    flock -x 9
    mv "$f" "$(dirname "$f")/history/${sid}.json"
  ) 9>"$lock"
  ss_init
}
