#!/bin/bash
# Shows ticket progression status for all departments.
# Also handles automatic done -> archive movement for completed sprints.
#
# Usage: sprint-progress.sh [--archive] [--json]
# Options:
#   --archive   Move tickets from done/ to archive/ (cleanup completed work)
#   --json      Output as JSON

COMPANY_DIR="${COMPANY_DIR:-.company}"
source "$(dirname "$0")/ticket-paths.sh"
ARCHIVE=false
JSON=false

while [ $# -gt 0 ]; do
  case "$1" in
    --archive) ARCHIVE=true; shift ;;
    --json) JSON=true; shift ;;
    --dept) TARGET_DEPT="$2"; shift 2 ;;
    *) shift ;;
  esac
done

DEPT_DIR="$COMPANY_DIR/departments"

if [ "$JSON" = true ]; then
  python3 -c "
import json, os, glob

result = {'departments': {}, 'totals': {'backlog': 0, 'sprint-current': 0, 'in-progress': 0, 'in-review': 0, 'done': 0, 'archive': 0}}

for dept in sorted(os.listdir('$DEPT_DIR')):
    tdir = os.path.join('$DEPT_DIR', dept, 'tickets')
    if not os.path.isdir(tdir):
        continue
    counts = {}
    for status in ['backlog', 'sprint-current', 'in-progress', 'in-review', 'done', 'archive']:
        sdir = os.path.join(tdir, status)
        c = len(glob.glob(os.path.join(sdir, '*.json'))) if os.path.isdir(sdir) else 0
        counts[status] = c
        result['totals'][status] = result['totals'].get(status, 0) + c
    counts['active'] = counts['sprint-current'] + counts['in-progress']
    result['departments'][dept] = counts

print(json.dumps(result, indent=2))
"
else
  printf "%-30s %7s %7s %7s %7s %7s %7s\n" "Department" "Backlog" "Sprint" "Active" "Review" "Done" "Archive"
  printf "%-30s %7s %7s %7s %7s %7s %7s\n" "------------------------------" "-------" "-------" "-------" "-------" "-------" "-------"

  for dept_path in "$DEPT_DIR"/*/; do
    dept=$(basename "$dept_path")
    tdir="$dept_path/tickets"
    [ -d "$tdir" ] || continue

    bl=$(find "$tdir/backlog" -name "*.json" 2>/dev/null | wc -l)
    sc=$(find "$tdir/sprint-current" -name "*.json" 2>/dev/null | wc -l)
    ip=$(find "$tdir/in-progress" -name "*.json" 2>/dev/null | wc -l)
    ir=$(find "$tdir/in-review" -name "*.json" 2>/dev/null | wc -l)
    dn=$(find "$tdir/done" -name "*.json" 2>/dev/null | wc -l)
    ar=$(find "$tdir/archive" -name "*.json" 2>/dev/null | wc -l)

    printf "%-30s %7d %7d %7d %7d %7d %7d\n" "$dept" "$bl" "$sc" "$((ip))" "$ir" "$dn" "$ar"
  done
fi

# Archive done tickets if requested (for a specific department, or all if no --dept)
if [ "$ARCHIVE" = true ]; then
  archived=0
  for dept_path in "$DEPT_DIR"/*/; do
    dept=$(basename "$dept_path")
    if [ -n "$TARGET_DEPT" ] && [ "$dept" != "$TARGET_DEPT" ]; then continue; fi
    tdir="$dept_path/tickets"
    [ -d "$tdir/done" ] || continue
    mkdir -p "$tdir/archive"
    for f in "$tdir"/done/*.json; do
      [ -f "$f" ] || continue
      mv "$f" "$tdir/archive/"
      archived=$((archived + 1))
    done
  done
  echo "Archived $archived completed tickets"
fi
