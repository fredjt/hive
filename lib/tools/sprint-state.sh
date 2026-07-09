#!/usr/bin/env bash
# sprint-state.sh — sourced helpers for reading/writing .company/sprints/current.json.
#
# The sprint object replaces the implicit "sprint = sprint-current/" contract
# with a first-class lifecycle state. Stages are explicit and the round-runner
# / stage-advance scripts dispatch on `.stage`.
#
# Usage (in another script):
#   . "$SKILL_DIR/lib/tools/sprint-state.sh"
#   ss_init "$COMPANY_DIR"          # ensures sprints/current.json exists
#   stage=$(ss_get_stage)           # current stage name
#   ss_set_stage tdd-red            # transition; updates stage_started_at
#   n=$(ss_active_ticket_count)     # tickets in sprint-current+in-progress+in-review
#   ss_archive_and_start_new        # called at END
#
# Single source of truth for stage names — kept in sync with stage-advance.sh.
# Add a new stage here AND in stage-advance.sh's switch.

# All operations are flock'd on the file itself so concurrent stage-advance /
# round-runner reads don't tear writes.

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
  # Acquire an exclusive lock on FD 9 in the CALLING shell. Each helper
  # opens this fd in its own subshell so the lock is released when the
  # subshell exits. Earlier impl tried `bash -c '...'` here but that strips
  # PATH and broke jq lookups inside the locked block.
  local lock="$(ss_state_dir)/.lock"
  mkdir -p "$(ss_state_dir)"
  touch "$lock"
  echo "$lock"
}

ss_init() {
  # Create sprints/current.json if it doesn't exist. Idempotent.
  local dir; dir=$(ss_state_dir)
  local f;   f=$(ss_state_file)
  mkdir -p "$dir/history"
  if [ -f "$f" ]; then return 0; fi
  local sid="sprint-$(date -u +%Y%m%dT%H%M%SZ)"
  local now;  now=$(ss_now)
  cat > "$f" <<JSON
{
  "sprint_id": "$sid",
  "started_at": "$now",
  "stage": "ceo-plan",
  "stage_started_at": "$now",
  "stage_enqueued": false,
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
  # Generic getter. ss_get_field .release.version → reads .release.version
  local f; f=$(ss_state_file)
  local path="$1"
  [ -f "$f" ] || { echo ""; return 0; }
  jq -r "$path // empty" "$f"
}

ss__atomic_jq() {
  # ss__atomic_jq <jq-program> [--arg k v ...]
  # Reads ss_state_file, applies jq with args, writes back atomically.
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
  # Resetting stage_enqueued=false on every stage transition is the
  # contract that keeps stage-advance.sh's "enqueue current vs advance to
  # next" decision unambiguous. Don't change one without the other.
  ss__atomic_jq '.stage = $s | .stage_started_at = $t | .stage_enqueued = false' \
    --arg s "$new" --arg t "$(ss_now)"
}

ss_mark_enqueued() {
  ss__atomic_jq '.stage_enqueued = true'
}

ss_is_enqueued() {
  # Echoes "true" or "false". Direct read (no atomic_jq, that writes).
  local f; f=$(ss_state_file)
  [ -f "$f" ] || { echo "false"; return 0; }
  jq -r '.stage_enqueued // false' "$f" 2>/dev/null
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
  # ss_set_release_field test_log "/path/to/log"
  local key="$1" val="$2"
  ss__atomic_jq '.release[$k] = $v' --arg k "$key" --arg v "$val"
}

ss_set_ceo_direction_path() {
  ss__atomic_jq '.ceo_direction_path = $p' --arg p "$1"
}

ss_set_tickets_in_sprint() {
  # Pass a JSON array string: ss_set_tickets_in_sprint '["A","B","C"]'
  ss__atomic_jq '.tickets_in_sprint = $a' --argjson a "$1"
}

ss_active_ticket_count() {
  # Count tickets in sprint-current + in-progress + in-review across all depts.
  # Departments live under .company/departments/<dept>/tickets/<status>/*.json.
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
  # Print "<dept> <ticket_id>" pairs for in-review tickets without verified_by.
  # Used by orphan-recovery.sh.
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
  # Move current.json to history/<sprint_id>.json and create a fresh one.
  # Also sweep every dept's done/*.json into archive/ — without that sweep,
  # ticket files accumulate in done/ forever and the boards stay cluttered
  # with last-sprint's resolved tickets every sprint after.
  local f; f=$(ss_state_file)
  [ -f "$f" ] || { ss_init; return 0; }
  ss__atomic_jq '.stage = "complete" | .ended_at = $t' --arg t "$(ss_now)"
  local sid; sid=$(jq -r '.sprint_id' "$f")

  # Resolve $SKILL_DIR / $PROJECT_ROOT defensively — sprint-state.sh is
  # sourced by many callers and not all set both in every invocation.
  local skill_dir="${SKILL_DIR:-${HIVE_ROOT:-}}"
  local proj_root="${PROJECT_ROOT:-${COMPANY_DIR%/.company}}"
  if [ -n "$skill_dir" ] && [ -x "$skill_dir/lib/tools/sprint-archive-done.sh" ] \
     && [ -n "$proj_root" ]; then
    bash "$skill_dir/lib/tools/sprint-archive-done.sh" "$proj_root" "$sid" \
      >> "${COMPANY_DIR}/logs/stage-advance.log" 2>&1 \
      || echo "ss_archive_and_start_new: sprint-archive-done.sh failed" >&2
  fi

  local lock; lock=$(ss__lock_fd)
  (
    flock -x 9
    mv "$f" "$(dirname "$f")/history/${sid}.json"
  ) 9>"$lock"
  ss_init
}
