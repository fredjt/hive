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
    LIMIT="$LIMIT" ROUNDS_DIR="$ROUNDS_DIR" COMPANY_DIR="$COMPANY_DIR" python3 - <<'PYEOF'
import json, os, glob, datetime
limit = int(os.environ["LIMIT"])
rdir = os.environ["ROUNDS_DIR"]
cdir = os.environ.get("COMPANY_DIR", "")

def _parse_ts(s):
  try:
    return datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
  except Exception:
    return None

# B3 (0.9.0): per-round cost, aggregated at query time from the LLM usage
# log (entries whose ts falls inside the round's started_at..ended_at
# window). No new state is written; readers without a log get 0/0/0.
# The legacy llm-tiny.log name is accepted for one release, same
# convention as 'hive llm stats'.
usage = []
uf = None
if cdir:
  for name in ("llm-usage.log", "llm-tiny.log"):
    cand = os.path.join(cdir, "logs", name)
    if os.path.isfile(cand):
      uf = cand
      break
if uf:
  try:
    for line in open(uf):
      line = line.strip()
      if not line:
        continue
      try:
        u = json.loads(line)
      except Exception:
        continue
      ts = _parse_ts(u.get("ts", ""))
      if ts is None:
        continue
      try:
        tokens = int(u.get("tokens") or 0)
      except (TypeError, ValueError):
        tokens = 0
      try:
        cost = float(u.get("cost_usd") or 0.0)
      except (TypeError, ValueError):
        cost = 0.0
      usage.append((ts, tokens, cost))
  except Exception:
    usage = []

def _window(sa, ea):
  calls = tokens = 0
  cost = 0.0
  if sa is None:
    return calls, tokens, cost
  for ts, t, c in usage:
    if ts >= sa and (ea is None or ts <= ea):
      calls += 1
      tokens += t
      cost += c
  return calls, tokens, cost

hist = sorted(glob.glob(os.path.join(rdir, "history", "*.json")))[-limit:]
out = []
totals = {"calls": 0, "tokens": 0, "cost_usd": 0.0}
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
  sa = _parse_ts(d.get("started_at"))
  ea = _parse_ts(d.get("ended_at"))
  if sa and ea:
    duration = int((ea - sa).total_seconds())
  calls, tokens, cost = _window(sa, ea)
  totals["calls"] += calls
  totals["tokens"] += tokens
  totals["cost_usd"] += cost
  out.append({
    "round_id":   d.get("round_id"),
    "source":     d.get("source"),
    "started_at": d.get("started_at"),
    "ended_at":   d.get("ended_at"),
    "duration_s": duration,
    "entry_count": len(entries),
    "status_counts": status_counts,
    "calls": calls,
    "tokens": tokens,
    "cost_usd": round(cost, 8),
  })
totals["cost_usd"] = round(totals["cost_usd"], 8)
print(json.dumps({"rounds": out, "totals": totals}, indent=2))
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
