#!/usr/bin/env bash
set -uo pipefail

# sprint-cost-summary.sh — write the "what did the sprint burn" report.
#
# Called from sprint-archive-done.sh when a sprint archives. Prices
# every round in rounds/history/ from the LLM usage log — entries whose
# timestamps fall inside the round's started_at/ended_at window, the
# same windowing /api/rounds/history uses — and writes ONE markdown
# report to .company/reports/sprint-reviews/
# via report-write.sh:
#
#   | round | entries | duration_s | tokens | cost_usd |
#
# plus a sprint total. Deterministic and idempotent: re-running the
# archive rewrites the same file with the same numbers. The answer to
# "what did this sprint cost" is a file, not a grep.
#
# Usage: sprint-cost-summary.sh <project-root> <sprint-id>

PROJECT_ROOT="${1:-${PROJECT_ROOT:-$PWD}}"
SPRINT_ID="${2:-${HIVE_SPRINT_ID:-unknown}}"
PROJECT_ROOT="$(cd "$PROJECT_ROOT" 2>/dev/null && pwd)" || { echo "sprint-cost-summary: bad project root" >&2; exit 2; }
COMPANY_DIR="${COMPANY_DIR:-$PROJECT_ROOT/.company}"
# Exported so report-write.sh (a child bash) resolves the same company
# dir instead of falling back to its CWD-relative `.company` default.
export COMPANY_DIR
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

[ -d "$COMPANY_DIR" ] || { echo "sprint-cost-summary: no company dir at $COMPANY_DIR" >&2; exit 2; }

REPORT=$(SPRINT_ID="$SPRINT_ID" COMPANY_DIR="$COMPANY_DIR" \
         ROUNDS_DIR="$COMPANY_DIR/rounds/history" python3 - <<'PYEOF'
import json, os, glob, datetime

sid = os.environ.get("SPRINT_ID", "unknown")
cdir = os.environ.get("COMPANY_DIR", "")
rdir = os.environ.get("ROUNDS_DIR", "")

def _parse_ts(s):
    try:
        return datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None

# Log discovery: llm-usage.log (legacy llm-tiny.log honored for one
# release); bad lines skipped.
usage = []
for name in ("llm-usage.log", "llm-tiny.log"):
    uf = os.path.join(cdir, "logs", name)
    if os.path.isfile(uf):
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
        break

rows = []
tot_tokens = 0
tot_cost = 0.0
for f in sorted(glob.glob(os.path.join(rdir, "*.json"))):
    try:
        d = json.load(open(f))
    except Exception:
        continue
    rid = d.get("round_id") or os.path.basename(f)
    sa = _parse_ts(d.get("started_at"))
    ea = _parse_ts(d.get("ended_at"))
    duration = None
    if sa and ea:
        duration = int((ea - sa).total_seconds())
    tokens = 0
    cost = 0.0
    if sa is not None:
        for ts, t, c in usage:
            if ts >= sa and (ea is None or ts <= ea):
                tokens += t
                cost += c
    rows.append((rid, len(d.get("entries") or []), duration, tokens, cost))
    tot_tokens += tokens
    tot_cost += cost

lines = [
    f"# Sprint cost summary — {sid}",
    "",
    "| round | entries | duration_s | tokens | cost_usd |",
    "|---|---|---|---|---|",
]
for rid, n, dur, tokens, cost in rows:
    lines.append(f"| {rid} | {n} | {dur if dur is not None else '-'} | {tokens} | {cost:.6f} |")
lines += [
    "",
    "## Totals",
    f"- rounds: {len(rows)}",
    f"- tokens: {tot_tokens}",
    f"- cost_usd: {tot_cost:.6f}",
]
print("\n".join(lines))
PYEOF
) || { echo "sprint-cost-summary: failed to build the report" >&2; exit 1; }

bash "$SCRIPT_DIR/report-write.sh" sprint-review "${SPRINT_ID}-cost-summary.md" \
  --content "$REPORT" \
  || { echo "sprint-cost-summary: report-write.sh failed" >&2; exit 1; }
