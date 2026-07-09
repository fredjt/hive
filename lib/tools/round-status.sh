#!/usr/bin/env bash
set -uo pipefail

# round-status.sh — read-only JSON dump of the round queue. Powers
# /api/rounds/* in the dashboard and the `hive round status` CLI.
#
# Usage:
#   round-status.sh --current               # full current-round.json
#   round-status.sh --next                  # full next-plan.json (or {})
#   round-status.sh --history [N]           # summarized last N rounds (default 20)
#   round-status.sh --round <round_id>      # full historical round JSON
#   round-status.sh --force-advance         # admin: skip the running entry

COMPANY_DIR="${COMPANY_DIR:-${PROJECT_ROOT:-$PWD}/.company}"
ROUNDS_DIR="$COMPANY_DIR/rounds"
LOCK="$ROUNDS_DIR/queue-lock"

cmd="${1:-}"
case "$cmd" in
  --current)
    f="$ROUNDS_DIR/current-round.json"
    [ -f "$f" ] && cat "$f" || echo '{}'
    ;;

  --next)
    f="$ROUNDS_DIR/next-plan.json"
    [ -f "$f" ] && cat "$f" || echo '{}'
    ;;

  --history)
    LIMIT="${2:-20}"
    LIMIT="$LIMIT" ROUNDS_DIR="$ROUNDS_DIR" python3 - <<'PYEOF'
import json, os, sys, glob
limit = int(os.environ["LIMIT"])
rdir = os.environ["ROUNDS_DIR"]
hist = sorted(glob.glob(os.path.join(rdir, "history", "*.json")))[-limit:]
out = []
for f in reversed(hist):
  try:
    d = json.load(open(f))
  except Exception:
    continue
  entries = d.get("entries", []) or []
  status_counts = {}
  for e in entries:
    s = e.get("status", "?")
    status_counts[s] = status_counts.get(s, 0) + 1
  duration = None
  if d.get("started_at") and d.get("ended_at"):
    try:
      import datetime
      sa = datetime.datetime.fromisoformat(d["started_at"].replace("Z","+00:00"))
      ea = datetime.datetime.fromisoformat(d["ended_at"].replace("Z","+00:00"))
      duration = int((ea - sa).total_seconds())
    except Exception:
      pass
  out.append({
    "round_id":   d.get("round_id"),
    "source":     d.get("source"),
    "started_at": d.get("started_at"),
    "ended_at":   d.get("ended_at"),
    "duration_s": duration,
    "entry_count": len(entries),
    "status_counts": status_counts,
  })
print(json.dumps({"rounds": out}, indent=2))
PYEOF
    ;;

  --round)
    RID="${2:-}"
    [ -z "$RID" ] && { echo '{"error":"missing round id"}'; exit 2; }
    f="$ROUNDS_DIR/history/${RID}.json"
    [ -f "$f" ] && cat "$f" || echo "{\"error\":\"round not found: ${RID}\"}"
    ;;

  --force-advance)
    [ -f "$LOCK" ] || touch "$LOCK"
    exec 7>"$LOCK"
    flock -x 7
    f="$ROUNDS_DIR/current-round.json"
    [ -f "$f" ] || { echo '{"ok":false,"error":"no current round"}'; exit 1; }
    FILE="$f" python3 - <<'PYEOF'
import json, os
f = os.environ["FILE"]
d = json.load(open(f))
entries = d.get("entries", [])
cur = d.get("cursor", 0)
if cur < len(entries):
  e = entries[cur]
  if e.get("status") in ("queued","running"):
    e["status"] = "skipped"
    e["skip_reason"] = "force-advance"
  d["cursor"] = cur + 1
  tmp = f + ".tmp"
  json.dump(d, open(tmp,"w"), indent=2)
  os.replace(tmp, f)
  print(json.dumps({"ok": True, "advanced_past": e.get("id")}))
else:
  print(json.dumps({"ok": False, "error": "queue already at end"}))
PYEOF
    flock -u 7
    ;;

  *)
    echo "Usage: round-status.sh --current|--next|--history [N]|--round <id>|--force-advance" >&2
    exit 2
    ;;
esac
