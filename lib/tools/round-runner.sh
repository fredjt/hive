#!/usr/bin/env bash
set -uo pipefail

# round-runner.sh — single-threaded FIFO drainer. The ONLY consumer of
# claude-spawn.sh in the running company.
#
# Loop:
#   while true:
#     entry = read next queued entry from current-round.json
#     if no entry:
#       finalize round
#       if just-drained-standup: round-promote.sh ; continue
#       else:                    standup-runner.sh --enqueue-only ; continue
#     if should_skip(entry): mark skipped, continue
#     mark running ; spawn claude (synchronous) ; mark done
#     advance cursor
#
# Singleton-locked via flock on rounds/runner.pid. A second invocation
# exits 0 immediately.
#
# Usage: round-runner.sh <project-root>

PROJECT_ROOT="${1:-.}"
PROJECT_ROOT="$(cd "$PROJECT_ROOT" && pwd)"
SKILL_DIR="${HIVE_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}"
COMPANY_DIR="$PROJECT_ROOT/.company"
ROUNDS_DIR="$COMPANY_DIR/rounds"
ROUND="$ROUNDS_DIR/current-round.json"
QUEUE_LOCK="$ROUNDS_DIR/queue-lock"
RUNNER_PID="$ROUNDS_DIR/runner.pid"
LOG="$COMPANY_DIR/logs/round-runner.log"

mkdir -p "$ROUNDS_DIR/history" "$COMPANY_DIR/logs"
touch "$QUEUE_LOCK"
export COMPANY_DIR PROJECT_ROOT SKILL_DIR HIVE_ROOT="$SKILL_DIR"

HIVE_ENV_FILE="${HIVE_ENV_FILE:-$HOME/.config/hive/env.sh}"
[ -f "$HIVE_ENV_FILE" ] && . "$HIVE_ENV_FILE"

# Singleton enforcement.
#
# 1) If a previous round-runner's PID is recorded AND that process is
#    actually alive, treat it as a stale survivor (company-stop missed it
#    because its pattern list is incomplete or processes.json was stale).
#    SIGTERM it, brief grace, SIGKILL if needed, then take the lock.
# 2) flock -n on the pid file as the canonical lock. If somehow another
#    round-runner is running anyway (NFS, lock semantics, race), exit 0.
PRIOR_PID=$(cat "$RUNNER_PID" 2>/dev/null | head -1 | tr -dc '0-9')
if [ -n "$PRIOR_PID" ] && [ "$PRIOR_PID" != "$$" ] && kill -0 "$PRIOR_PID" 2>/dev/null; then
  log_pre() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] [runner] $*" >> "$LOG"; }
  log_pre "found stale round-runner pid=$PRIOR_PID; sending SIGTERM"
  kill -TERM "$PRIOR_PID" 2>/dev/null || true
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    kill -0 "$PRIOR_PID" 2>/dev/null || break
    sleep 0.5
  done
  if kill -0 "$PRIOR_PID" 2>/dev/null; then
    log_pre "stale round-runner pid=$PRIOR_PID didn't exit; SIGKILL"
    kill -KILL "$PRIOR_PID" 2>/dev/null || true
    sleep 0.3
  fi
fi

exec 9>"$RUNNER_PID"
if ! flock -n 9; then
  # Identify the actual holder so a leaked-fd orphan (e.g., a Claude subprocess
  # that inherited fd 9 from a since-dead parent runner) doesn't silently lock
  # us out. Without this report the user sees "hive start did nothing" with no
  # explanation — happened on photonic-proc when a round-runner died mid-CEO
  # turn and Claude kept the lock alive.
  HOLDER_PID=""
  if command -v fuser >/dev/null 2>&1; then
    HOLDER_PID=$(fuser "$RUNNER_PID" 2>/dev/null | tr -d ' ' | head -1)
  fi
  if [ -z "$HOLDER_PID" ] && command -v lsof >/dev/null 2>&1; then
    HOLDER_PID=$(lsof -t "$RUNNER_PID" 2>/dev/null | head -1)
  fi
  if [ -n "$HOLDER_PID" ]; then
    HOLDER_CMD=$(ps -o pid=,comm=,args= -p "$HOLDER_PID" 2>/dev/null | head -1)
    {
      echo "another round-runner lock is held by pid=$HOLDER_PID"
      echo "  $HOLDER_CMD"
      echo "  If that's an orphaned Claude subprocess (parent runner died mid-turn"
      echo "  and the child kept fd 9 open), kill it: kill $HOLDER_PID"
      echo "  Then re-run 'hive start'."
    } >&2
    log_pre() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] [runner] $*" >> "$LOG"; }
    log_pre "lock held by pid=$HOLDER_PID — exiting (cmd: $HOLDER_CMD)"
  else
    echo "another round-runner is active (lock holder unknown)" >&2
  fi
  exit 0
fi
echo $$ >&9

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { echo "[$(ts)] [runner] $*" >> "$LOG"; }

log "starting (pid=$$, project=$PROJECT_ROOT)"

# Bootstrap: build first-boot round if no current
if [ ! -f "$ROUND" ]; then
  log "no current-round.json; bootstrapping --first-boot"
  bash "$SKILL_DIR/lib/tools/round-promote.sh" "$PROJECT_ROOT" --first-boot \
    >> "$LOG" 2>&1 || true
fi

# Per-ticket verification bounce limit — mirrors the config in
# stage-advance.sh so the runner can enforce the same cap when
# evaluating verification entries.
MAX_VERIFICATION_BOUNCES="${HIVE_MAX_VERIFICATION_BOUNCES:-5}"

# read_next_entry: returns the JSON of the entry at cursor (status=queued
# or running/orphaned), OR empty string if cursor >= len(entries).
read_next_entry() {
  exec 7>"$QUEUE_LOCK"; flock -x 7
  local out
  out=$(python3 - <<PYEOF 2>/dev/null
import json, sys, os
f = "$ROUND"
if not os.path.exists(f):
  sys.exit(0)
d = json.load(open(f))
entries = d.get("entries", [])
cur = d.get("cursor", 0)
if cur >= len(entries):
  sys.exit(0)
e = entries[cur]
# Crash recovery: if status=running and spawn_id has no active record, mark failed
if e.get("status") == "running":
  spawn_id = e.get("spawn_id")
  active_dir = os.path.join(os.path.dirname(f), "..", "logs", "active")
  found = False
  if spawn_id:
    af = os.path.join(active_dir, spawn_id + ".json")
    found = os.path.exists(af)
  if not found:
    e["status"] = "failed"
    e["skip_reason"] = "orphaned (runner restart found no active record)"
    d["cursor"] = cur + 1
    json.dump(d, open(f + ".tmp","w"), indent=2)
    os.replace(f + ".tmp", f)
    sys.exit(0)
print(json.dumps(e))
PYEOF
)
  flock -u 7
  echo "$out"
}

# Update specific fields on an entry by id
update_entry() {
  local entry_id="$1"; shift
  local kvs="$*"   # space-separated key=value pairs
  exec 7>"$QUEUE_LOCK"; flock -x 7
  ENTRY_ID="$entry_id" KVS="$kvs" FILE="$ROUND" python3 - <<'PYEOF'
import json, os
f = os.environ["FILE"]
eid = os.environ["ENTRY_ID"]
kvs = os.environ["KVS"].split()
d = json.load(open(f))
for e in d.get("entries", []):
  if e.get("id") == eid:
    for kv in kvs:
      if "=" not in kv: continue
      k, v = kv.split("=", 1)
      # numeric coercion
      try:
        if v == "null": v = None
        elif v.isdigit(): v = int(v)
        elif v.replace(".","",1).isdigit(): v = float(v)
      except Exception:
        pass
      e[k] = v
    break
json.dump(d, open(f + ".tmp","w"), indent=2)
os.replace(f + ".tmp", f)
PYEOF
  flock -u 7
}

advance_cursor() {
  exec 7>"$QUEUE_LOCK"; flock -x 7
  FILE="$ROUND" python3 - <<'PYEOF'
import json, os
f = os.environ["FILE"]
d = json.load(open(f))
d["cursor"] = d.get("cursor", 0) + 1
json.dump(d, open(f + ".tmp","w"), indent=2)
os.replace(f + ".tmp", f)
PYEOF
  flock -u 7
}

# Returns the kind of the entry whose cursor index is (cursor - 1), used to
# detect "we just finished standup-synth → next step is round-promote".
last_drained_kind() {
  python3 - <<PYEOF 2>/dev/null
import json, os
d = json.load(open("$ROUND"))
entries = d.get("entries", [])
cur = d.get("cursor", 0)
if cur > 0 and cur - 1 < len(entries):
  print(entries[cur-1].get("kind", ""))
PYEOF
}

# Whether the round has any standup-synth entries at all (so we can detect
# "we already enqueued standup this round, don't re-enqueue").
has_standup() {
  python3 - <<PYEOF 2>/dev/null
import json
d = json.load(open("$ROUND"))
for e in d.get("entries", []):
  if e.get("kind") in ("standup-brief", "standup-synth"):
    print("yes"); break
PYEOF
}

should_skip_entry() {
  local entry_json="$1"
  local kind dept ticket
  kind=$(echo "$entry_json"   | python3 -c "import sys,json;print(json.load(sys.stdin).get('kind',''))" 2>/dev/null)
  dept=$(echo "$entry_json"   | python3 -c "import sys,json;print(json.load(sys.stdin).get('dept',''))" 2>/dev/null)
  ticket=$(echo "$entry_json" | python3 -c "import sys,json;print(json.load(sys.stdin).get('ticket') or '')" 2>/dev/null)
  case "$kind" in
    vp-cycle)
      if [ -x "$SKILL_DIR/lib/tools/vp-idle-check.sh" ] && [ -n "$dept" ]; then
        if ! COMPANY_DIR="$COMPANY_DIR" bash "$SKILL_DIR/lib/tools/vp-idle-check.sh" "$dept" >/dev/null 2>&1; then
          echo "vp-idle-check skip"
          return 0
        fi
      fi
      ;;
    ic-cycle)
      [ -z "$ticket" ] && return 1
      # Skip if ticket no longer in sprint-current/in-progress
      local found=0
      for st in sprint-current in-progress; do
        if [ -f "$COMPANY_DIR/departments/$dept/tickets/$st/${ticket}.json" ]; then
          found=1; break
        fi
      done
      if [ "$found" -eq 0 ]; then
        echo "ticket no longer in sprint-current/in-progress"
        return 0
      fi
      ;;
    verification)
      [ -z "$ticket" ] && return 1
      local tf="$COMPANY_DIR/departments/$dept/tickets/in-review/${ticket}.json"
      if [ ! -f "$tf" ]; then
        echo "ticket no longer in in-review"
        return 0
      fi
      if ! jq -e '.gates_passed == true' "$tf" >/dev/null 2>&1; then
        echo "gates_passed != true"
        return 0
      fi
      if jq -e '.verified_by != null and .verified_by != ""' "$tf" >/dev/null 2>&1; then
        echo "already verified"
        return 0
      fi
      # Safety net: skip if the ticket has exceeded the verification
      # bounce limit (the stage-enqueue guard should catch this first).
      local _bounce
      _bounce=$(jq -r '.verification_bounce_count // 0' "$tf" 2>/dev/null)
      if [ "${_bounce:-0}" -ge "${MAX_VERIFICATION_BOUNCES:-5}" ]; then
        echo "verification_bounce_limit ($_bounce >= ${MAX_VERIFICATION_BOUNCES:-5})"
        return 0
      fi
      ;;
  esac
  return 1
}

# Main loop. EMPTY_DRAIN_STREAK counts consecutive drains that produced
# no work — when stage-advance loops through tdd-red → tdd-green → verify
# without enqueuing anything (e.g., waiting for the verification-gates
# daemon to set gates_passed on tickets we just reconciled), we'd otherwise
# busy-loop at full CPU. Sleep grows with the streak.
EMPTY_DRAIN_STREAK=0
while true; do
  # Defensive: if .company got corrupted mid-run (agent replaced it with
  # a self-symlink, removed it, etc.), refuse to keep iterating — every
  # subsequent operation would either ELOOP or generate stale state.
  # Better to halt loudly so the operator sees the recovery hint.
  if [ -x "$SKILL_DIR/lib/tools/check-company-dir.sh" ]; then
    bash "$SKILL_DIR/lib/tools/check-company-dir.sh" "$PROJECT_ROOT" >/dev/null 2>&1 || {
      log "FATAL: .company is corrupted (symlink loop or missing). Halting round-runner."
      log "       Run 'bash $SKILL_DIR/lib/tools/check-company-dir.sh $PROJECT_ROOT' for recovery steps."
      bash "$SKILL_DIR/lib/tools/check-company-dir.sh" "$PROJECT_ROOT" 2>&1 | tee -a "$LOG" >&2
      exit 1
    }
  fi

  ENTRY=$(read_next_entry)

  if [ -z "$ENTRY" ]; then
    # Queue drained. Archive current round, then run stage-advance to
    # decide what runs next (sprint-staged lifecycle).
    log "queue drained; archiving + advancing stage"
    bash "$SKILL_DIR/lib/tools/round-promote.sh" "$PROJECT_ROOT" \
      >> "$LOG" 2>&1 || log "round-promote exited nonzero"
    bash "$SKILL_DIR/lib/tools/stage-advance.sh" "$PROJECT_ROOT" \
      >> "$LOG" 2>&1 || log "stage-advance exited nonzero"

    # Check whether stage-advance enqueued anything. The new round's
    # entries[] count tells us. Same drain → no work → back-off.
    NEW_ENTRY_COUNT=$(jq '.entries | length' "$ROUND" 2>/dev/null || echo 0)
    if [ "$NEW_ENTRY_COUNT" = "0" ]; then
      EMPTY_DRAIN_STREAK=$((EMPTY_DRAIN_STREAK + 1))
      # 1s, then 5s, then 15s, then 30s (capped). Lets the 90s
      # verification-gates daemon catch up to set gates_passed on freshly-
      # reconciled tickets, lets a stuck human notice and intervene, etc.
      if   [ "$EMPTY_DRAIN_STREAK" -le 2 ]; then SLEEP=1
      elif [ "$EMPTY_DRAIN_STREAK" -le 5 ]; then SLEEP=5
      elif [ "$EMPTY_DRAIN_STREAK" -le 10 ]; then SLEEP=15
      else SLEEP=30
      fi
      log "stage-advance enqueued 0 entries (streak=$EMPTY_DRAIN_STREAK); sleeping ${SLEEP}s"
      sleep "$SLEEP"
    else
      EMPTY_DRAIN_STREAK=0
    fi
    continue
  fi

  ENTRY_ID=$(echo "$ENTRY" | python3 -c "import sys,json;print(json.load(sys.stdin)['id'])")
  KIND=$(echo    "$ENTRY" | python3 -c "import sys,json;print(json.load(sys.stdin)['kind'])")
  AGENT=$(echo   "$ENTRY" | python3 -c "import sys,json;print(json.load(sys.stdin).get('agent',''))")
  DEPT=$(echo    "$ENTRY" | python3 -c "import sys,json;print(json.load(sys.stdin).get('dept',''))")
  ROLE=$(echo    "$ENTRY" | python3 -c "import sys,json;print(json.load(sys.stdin).get('role',''))")
  TICKET=$(echo  "$ENTRY" | python3 -c "import sys,json;print(json.load(sys.stdin).get('ticket') or '')")
  LOG_TO=$(echo  "$ENTRY" | python3 -c "import sys,json;print(json.load(sys.stdin).get('log_to') or '')")
  PROMPT_EXTRA=$(echo "$ENTRY" | python3 -c "import sys,json;print(json.load(sys.stdin).get('prompt_extra') or '')")
  PROMPT_INLINE=$(echo "$ENTRY" | python3 -c "import sys,json;print(json.load(sys.stdin).get('prompt_inline') or '')")

  # Skip rules
  if reason=$(should_skip_entry "$ENTRY"); then
    log "skip $ENTRY_ID ($KIND $AGENT/$DEPT): $reason"
    update_entry "$ENTRY_ID" "status=skipped" "skip_reason=$reason"
    advance_cursor
    continue
  fi

  STARTED=$(ts)
  STARTED_TS=$(date +%s)
  SPAWN_ID="round-$ENTRY_ID"
  log "RUN  $ENTRY_ID kind=$KIND agent=$AGENT dept=$DEPT role=$ROLE ticket=${TICKET:-none}"
  update_entry "$ENTRY_ID" "status=running" "started_at=$STARTED" "spawn_id=$SPAWN_ID"

  # ── Pre-spawn ticket move + worktree provisioning (sprint-staged lifecycle) ──
  # Sprint-current → in-progress when an ic-cycle-tester picks up a ticket.
  # Both ic-cycle-tester and ic-cycle-eng also get an idempotent worktree+branch
  # ensure so the agent's prompt claim ("worktree+branch exist") is true and the
  # agent never reaches for `git-worktree.sh create` themselves (which used to
  # destroy active worktrees with --force).
  # Order matters: create the worktree FIRST so ticket-move's git
  # detection (which scans for $PROJECT_ROOT/.worktrees/$TICKET_ID) finds
  # it on the same call and stamps the ticket's resolution.git_branch /
  # resolution.worktree / resolution.commit fields immediately. With the
  # old order (move first, then create), the move couldn't see the
  # worktree yet and the dashboard's "Resolution" link stayed blank
  # through TDD-RED and TDD-GREEN.
  if [ "$KIND" = "ic-cycle-tester" ] || [ "$KIND" = "ic-cycle-eng" ]; then
    if [ -n "$TICKET" ] && [ -x "$SKILL_DIR/lib/tools/git-worktree.sh" ]; then
      bash "$SKILL_DIR/lib/tools/git-worktree.sh" create "$TICKET" "$PROJECT_ROOT" \
        >> "$LOG" 2>&1 || log "  pre-spawn worktree-ensure for $TICKET failed (continuing)"
    fi
  fi
  if [ "$KIND" = "ic-cycle-tester" ] && [ -n "$TICKET" ]; then
    HIVE_SCRIPTED_MOVE=1 HIVE_ROLE=System HIVE_AGENT=round-runner \
      bash "$SKILL_DIR/lib/tools/ticket-move.sh" \
        "$DEPT" "$TICKET" in-progress \
        "Started TDD-RED phase" "round-runner" \
        >> "$LOG" 2>&1 || log "  pre-spawn ticket-move for $TICKET failed (ticket may already be in-progress)"
  fi

  # Build prompt. Even inline prompts (assembled by stage-enqueue scripts)
  # already include the framework primer prepended via load_agent_prompt;
  # this branch handles legacy / non-stage-enqueue paths and prepends the
  # primer here so every spawn sees consistent framework context.
  . "$SKILL_DIR/lib/tools/load-agent-prompt.sh"
  if [ -n "$PROMPT_INLINE" ]; then
    PROMPT="$PROMPT_INLINE"
  else
    AGENT_MD="$SKILL_DIR/lib/agents/${AGENT}.md"
    if [ ! -f "$AGENT_MD" ]; then
      log "agent prompt missing: $AGENT_MD — failing entry"
      update_entry "$ENTRY_ID" "status=failed" "skip_reason=missing-agent-prompt"
      advance_cursor
      continue
    fi
    PROMPT="$(load_agent_prompt "$AGENT_MD")

--- Assignment ---
Department: $DEPT
Project root: $PROJECT_ROOT
SKILL_DIR: $SKILL_DIR"
    [ -n "$TICKET" ] && PROMPT="$PROMPT
Ticket: $TICKET"
    [ -n "$PROMPT_EXTRA" ] && PROMPT="$PROMPT

$PROMPT_EXTRA"
  fi

  [ -n "$LOG_TO" ] || LOG_TO="$COMPANY_DIR/logs/${AGENT}.log"
  mkdir -p "$(dirname "$LOG_TO")"

  # Synchronous claude-spawn — this is the entire serialization.
  # `9<&-` closes fd 9 (the singleton-lock fd) in the child before exec, so
  # the long-running Claude subprocess does NOT inherit and hold the lock.
  # Without this, if the runner dies mid-turn (OOM, crash, hive stop), the
  # Claude child keeps the lock alive and every subsequent 'hive start'
  # silently exits with "another round-runner is active".
  HIVE_AGENT="$AGENT" HIVE_DEPT="$DEPT" HIVE_ROLE="$ROLE" HIVE_TICKET="$TICKET" \
  HIVE_ROUND_ENTRY="$ENTRY_ID" \
  bash "$SKILL_DIR/lib/core/claude-spawn.sh" -- "$PROMPT" \
    9<&- >> "$LOG_TO" 2>&1
  EC=$?
  ENDED=$(ts)
  ENDED_TS=$(date +%s)
  DUR=$((ENDED_TS - STARTED_TS))

  if [ $EC -eq 0 ]; then
    log "DONE $ENTRY_ID exit=0 duration=${DUR}s"
    update_entry "$ENTRY_ID" "status=done" "ended_at=$ENDED" "exit_code=0" "duration_s=$DUR"

    # ── Post-spawn handling for ic-cycle-tester (TDD-RED) ──
    # If the tester exited cleanly but produced NO [TDD-RED] $TID commit,
    # the next loop iteration will re-enqueue another tester for the same
    # ticket — forever. Bound this: count consecutive no-commit tester
    # runs in a `tdd_red_attempts` field on the ticket. After 3, set
    # skip_tdd_red=true so the lifecycle advances to TDD-GREEN with the
    # engineer responsible for both writing the test and the code.
    # (photonic-proc had ENGINEERING-0005 stuck at 5 tester runs with
    # no commit ever produced.)
    if [ "$KIND" = "ic-cycle-tester" ] && [ -n "$TICKET" ]; then
      WT="$PROJECT_ROOT/.worktrees/$TICKET"
      BR="feature/$TICKET"
      HAS_RED_COMMIT=0
      if git -C "$PROJECT_ROOT" log --all --oneline 2>/dev/null \
           | grep -qE "\[TDD-RED\][[:space:]]*$TICKET([:[:space:]]|$)"; then
        HAS_RED_COMMIT=1
      fi
      if [ "$HAS_RED_COMMIT" = "0" ]; then
        TFILE=""
        for st in sprint-current in-progress in-review; do
          cand="$COMPANY_DIR/departments/$DEPT/tickets/$st/$TICKET.json"
          [ -f "$cand" ] && TFILE="$cand" && break
        done
        if [ -n "$TFILE" ]; then
          ATTEMPTS=$(jq -r '.tdd_red_attempts // 0' "$TFILE" 2>/dev/null)
          ATTEMPTS=$((ATTEMPTS + 1))
          tmp="${TFILE}.tmp.$$"
          jq --argjson n "$ATTEMPTS" '.tdd_red_attempts = $n' "$TFILE" > "$tmp" && mv "$tmp" "$TFILE"
          log "  ic-tester exited but produced no [TDD-RED] $TICKET commit (attempt $ATTEMPTS)"
          if [ "$ATTEMPTS" -ge 3 ]; then
            # Promote to engineer-handles-everything mode and post a comment
            # so the engineer's prompt sees what happened.
            jq '.skip_tdd_red = true' "$TFILE" > "$tmp" && mv "$tmp" "$TFILE"
            bash "$SKILL_DIR/lib/tools/ticket-comment.sh" "$DEPT" "$TICKET" "round-runner" \
              "ic-tester ran $ATTEMPTS times for this ticket without producing a [TDD-RED] $TICKET commit. Setting skip_tdd_red=true so the engineer (next stage) writes both the test AND the implementation in one go. This prevents the busy-loop where every Loop A iteration re-spawns a tester that exits without committing." \
              >> "$LOG" 2>&1 || true
            log "  skip_tdd_red=true set on $TICKET after $ATTEMPTS unsuccessful tester runs"
          fi
        fi
      fi
    fi

    # ── Post-spawn ticket move (sprint-staged lifecycle) ──
    # In-progress → in-review when an ic-cycle-eng exits cleanly. The PR
    # opens automatically as a ticket-move side-effect.
    #
    # Race mitigation: the verification-gates daemon (pulse-daemon.sh) runs
    # every 60-90s and can bounce in-review tickets back to in-progress at
    # any time. If the daemon bounces the ticket between the engineer's exit
    # and this move, we retry up to 3 times with brief delays — the ticket
    # may be bouncing back and forth as the daemon and runner race. If after
    # retries the ticket is already in in-review, that's the desired state
    # (the daemon passed it) so we succeed. If it's stuck in in-progress
    # after all retries, we log and continue rather than blocking the entire
    # post-spawn flow. (photonic-proc ENGINEERING-0054, INFRASTRUCTU-0055
    # hit this pattern: post-spawn ticket-move failed, ticket left in
    # in-progress with no re-enqueue since it already had a [TDD-GREEN]
    # commit, causing it to stall until the next sprint cycle.)
    _POST_SPAWN_RETRIES=3
    _POST_SPAWN_OK=0
    while [ "$_POST_SPAWN_RETRIES" -gt 0 ] && [ "$_POST_SPAWN_OK" -eq 0 ]; do
      _POST_SPAWN_RETRIES=$((_POST_SPAWN_RETRIES - 1))
      _MOVE_RC=0
      HIVE_SCRIPTED_MOVE=1 HIVE_ROLE=System HIVE_AGENT=round-runner \
        bash "$SKILL_DIR/lib/tools/ticket-move.sh" \
          "$DEPT" "$TICKET" in-review \
          "TDD-GREEN complete; engineer exited cleanly" "round-runner" \
          >> "$LOG" 2>&1 || _MOVE_RC=$?
      if [ "$_MOVE_RC" -eq 0 ]; then
        _POST_SPAWN_OK=1
        log "  post-spawn ticket-move for $TICKET succeeded (or already in target status)"
      else
        # Check if the daemon already moved it to in-review (daemon passed
        # gates and bounced it forward). If so, that's acceptable.
        _CURRENT_STATUS=$(jq -r '.status // "unknown"' \
          "$COMPANY_DIR/departments/$DEPT/tickets/in-review/$TICKET.json" 2>/dev/null || echo "unknown")
        if [ "$_CURRENT_STATUS" = "in-review" ]; then
          _POST_SPAWN_OK=1
          log "  post-spawn ticket-move for $TICKET: already in in-review (daemon advanced it)"
        elif [ "$_POST_SPAWN_RETRIES" -gt 0 ]; then
          log "  post-spawn ticket-move for $TICKET failed (rc=$_MOVE_RC), retrying in 2s..."
          sleep 2
        else
          log "  post-spawn ticket-move for $TICKET failed after $_POST_SPAWN_RETRIES retries (rc=$_MOVE_RC) — ticket may be stuck in wrong status"
        fi
      fi
    done

    # ── Force-refresh deterministic gates for this ticket ──────────
    # gates_passed may be stale-false from a prior conflict-rollback,
    # bounce, or fresh in-progress state. stage-enqueue-verify (which
    # fires on the very next queue-drain) requires gates_passed=true.
    # Without this synchronous re-run, the verification-gates daemon
    # would eventually catch up on its 90s loop — but orphan-recovery
    # bounces stuck-looking in-review tickets before the daemon does,
    # producing an infinite engineer-respawn loop. (photonic-proc
    # ENGINEERING-0031 hit this after a post-approve merge-conflict
    # rollback; engineer respawned 5+ times with no verifier between
    # iterations until the loop cap fired.)
    #
    # Retry loop: verification-gates can fail transiently if the daemon
    # is mid-bounce on the same ticket, or if gates fail for a different
    # ticket in the same dept (the script processes all in-review tickets
    # and exits nonzero on the first bounce). We retry up to 3 times
    # with 2s delays to let transient issues resolve.
    _GATES_RETRIES=3
    while [ "$_GATES_RETRIES" -gt 0 ]; do
      _GATES_RETRIES=$((_GATES_RETRIES - 1))
      _GATES_RC=0
      bash "$SKILL_DIR/lib/tools/verification-gates.sh" \
        "$DEPT" "$PROJECT_ROOT" --quiet >> "$LOG" 2>&1 || _GATES_RC=$?
      if [ "$_GATES_RC" -eq 0 ]; then
        log "  post-spawn verification-gates for $DEPT/$TICKET passed"
        break
      elif [ "$_GATES_RETRIES" -gt 0 ]; then
        log "  post-spawn verification-gates for $DEPT/$TICKET failed (rc=$_GATES_RC), retrying in 2s..."
        sleep 2
      else
        log "  post-spawn verification-gates for $DEPT/$TICKET failed after retries (rc=$_GATES_RC)"
      fi
    done

    # ── Mark any stale verification entry for this ticket as superseded ─
    # orphan-recovery.sh's verifier_ran_for() check treats ANY done
    # verification entry as "the verifier had a turn already." If a
    # PRIOR-iteration verifier approved and then ticket-move rolled
    # back on conflict, the stale entry is still in the round file. On
    # the upcoming verify stage, stage-enqueue-verify queues a fresh
    # verification entry — but if it doesn't (gates still failing for
    # some reason, race condition, etc.), orphan-recovery sees the
    # stale entry and bounces. Mark the stale entry kind=superseded
    # so verifier_ran_for() ignores it.
    if [ -f "$COMPANY_DIR/rounds/current-round.json" ]; then
      tmp="$COMPANY_DIR/rounds/current-round.json.tmp.$$"
      jq --arg t "$TICKET" '
        .entries |= map(
          if .kind == "verification" and .ticket == $t and .status == "done"
          then . + {kind: "verification-superseded"}
          else . end
        )
      ' "$COMPANY_DIR/rounds/current-round.json" > "$tmp" \
        && mv "$tmp" "$COMPANY_DIR/rounds/current-round.json" || true
    fi
  else
    log "FAIL $ENTRY_ID exit=$EC duration=${DUR}s"
    update_entry "$ENTRY_ID" "status=failed" "ended_at=$ENDED" "exit_code=$EC" "duration_s=$DUR"
  fi
  advance_cursor
done
