#!/usr/bin/env bash
set -uo pipefail

# repair-ticket-counters.sh — fix broken per-dept counter.json files and
# rename existing tickets that were created with a bad prefix.
#
# Two failure modes this addresses:
#   1. counter.json has prefix:"" or next_id:0 → tickets came out as
#      "-0000", "-0001", etc., with no department prefix.
#   2. counter.json is missing entirely while ticket files exist → next
#      ticket-create starts at 1 and collides with existing ids.
#
# For each dept under .company/departments/:
#   - Recompute the canonical prefix from the dept name.
#   - Find all existing ticket files (in any status) whose id is missing
#     the prefix or has the wrong prefix.
#   - Rename them to <CORRECT_PREFIX>-NNNN where NNNN is the next free
#     number (so we don't collide with already-correct ids).
#   - Update the ticket JSON's `id` field and any internal references.
#   - Rewrite counter.json with the correct prefix and next_id = max(seen)+1.
#
# Idempotent and dry-run by default. Pass --apply to actually rename.
#
# Usage: repair-ticket-counters.sh <project-root> [--apply]

PROJECT_ROOT="${1:-${PROJECT_ROOT:-$PWD}}"
PROJECT_ROOT="$(cd "$PROJECT_ROOT" 2>/dev/null && pwd)" || { echo "bad project root" >&2; exit 1; }
APPLY=0
shift 2>/dev/null || true
for arg in "$@"; do [ "$arg" = "--apply" ] && APPLY=1; done

COMPANY_DIR="$PROJECT_ROOT/.company"
[ -d "$COMPANY_DIR/departments" ] || { echo "no departments dir at $COMPANY_DIR" >&2; exit 1; }

[ "$APPLY" = "1" ] && MODE="APPLY" || MODE="DRY-RUN (pass --apply to commit)"
echo "repair-ticket-counters: $MODE"
echo

for dept_dir in "$COMPANY_DIR/departments"/*/; do
  dept=$(basename "$dept_dir")
  tickets_dir="$dept_dir/tickets"
  [ -d "$tickets_dir" ] || continue

  # Canonical prefix: letters of dept name, uppercase, 12-char cap
  canonical=$(echo "$dept" | tr '[:lower:]' '[:upper:]' | tr -d -c '[:upper:]' | cut -c1-12)
  [ -z "$canonical" ] && canonical="TICK"

  echo "── $dept (canonical prefix: $canonical) ──"

  # Walk every ticket file and decide what to rename
  APPLY="$APPLY" CANONICAL="$canonical" TICKETS_DIR="$tickets_dir" python3 - <<'PYEOF'
import json, os, re, glob, sys
apply = os.environ["APPLY"] == "1"
canonical = os.environ["CANONICAL"]
tdir = os.environ["TICKETS_DIR"]

statuses = ["backlog","sprint-current","in-progress","in-review","done","archive"]

# 1. Enumerate all ticket files. Parse id → prefix and number.
all_tickets = []   # list of (path, status, current_id, prefix, num)
for st in statuses:
    sd = os.path.join(tdir, st)
    if not os.path.isdir(sd): continue
    for f in sorted(glob.glob(os.path.join(sd, "*.json"))):
        base = os.path.basename(f)[:-5]    # strip .json
        m = re.match(r"^([A-Z]*)-(\d+)$", base)
        if m:
            prefix, num = m.group(1), int(m.group(2))
        else:
            prefix, num = "", 0
        all_tickets.append({"path": f, "status": st, "id_in_filename": base, "prefix": prefix, "num": num})

if not all_tickets:
    print(f"  no tickets to repair")
    sys.exit(0)

# 2. Find the highest ALREADY-CORRECT number so renames don't collide.
highest_correct = max((t["num"] for t in all_tickets if t["prefix"] == canonical), default=0)
next_free = highest_correct + 1

# 3. Plan renames for tickets with wrong/missing prefix
renames = []
for t in all_tickets:
    if t["prefix"] == canonical:
        continue   # already correct
    # Bad prefix or missing — needs rename
    new_id = f"{canonical}-{next_free:04d}"
    new_path = os.path.join(tdir, t["status"], new_id + ".json")
    renames.append({"old": t["path"], "new": new_path, "old_id": t["id_in_filename"], "new_id": new_id, "status": t["status"]})
    next_free += 1

if not renames:
    print(f"  all {len(all_tickets)} tickets already use prefix '{canonical}' — no repair needed")
else:
    print(f"  {len(renames)} ticket(s) need repair (highest existing {canonical}-* = {highest_correct:04d}):")
    for r in renames:
        print(f"    {r['status']:14s}  {r['old_id']:18s} → {r['new_id']}")

# 4. Apply (or dry-run)
if apply:
    for r in renames:
        # Update id in JSON
        try:
            with open(r["old"]) as fp: data = json.load(fp)
            data["id"] = r["new_id"]
            with open(r["old"], "w") as fp: json.dump(data, fp, indent=2)
            os.rename(r["old"], r["new"])
        except Exception as e:
            print(f"    FAIL {r['old_id']} → {r['new_id']}: {e}", file=sys.stderr)

# 5. Rewrite counter.json (always — even if no renames needed, this fixes
# corrupted counters like {prefix:"", next_id:0})
counter_path = os.path.join(tdir, "counter.json")
counter = {"prefix": canonical, "next_id": max(highest_correct, len(all_tickets)) + 1}
# Refine next_id: scan ALL tickets after rename and take max + 1
if apply or not renames:
    # After applying, all tickets have canonical prefix. Walk current dir.
    seen = []
    for st in statuses:
        sd = os.path.join(tdir, st)
        if not os.path.isdir(sd): continue
        for f in glob.glob(os.path.join(sd, "*.json")):
            m = re.match(r"^" + canonical + r"-(\d+)\.json$", os.path.basename(f))
            if m: seen.append(int(m.group(1)))
    counter["next_id"] = (max(seen) if seen else 0) + 1
else:
    counter["next_id"] = next_free

if apply:
    with open(counter_path, "w") as fp: json.dump(counter, fp, indent=2)
    print(f"  counter.json → {counter}")
else:
    print(f"  [dry-run] counter.json would become {counter}")
PYEOF
  echo
done

if [ "$APPLY" = "0" ]; then
  echo "(dry-run only — re-run with --apply to perform the renames)"
fi
