#!/bin/bash
# Usage: agent-cycle.sh <agent-role> <department>
# Increments per-agent cycle count and records timestamp.
# Agents call this at the start of each work cycle.
# Data stored in .company/logs/agent-cycles.json

COMPANY_DIR="${COMPANY_DIR:-.company}"
ROLE="$1"
DEPT="$2"

if [ -z "$ROLE" ]; then
  echo "Usage: agent-cycle.sh <agent-role> <department>" >&2
  exit 1
fi

CYCLE_FILE="$COMPANY_DIR/logs/agent-cycles.json"
mkdir -p "$(dirname "$CYCLE_FILE")"

TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
KEY="${ROLE}_${DEPT}"

# Env-var inputs + single-quoted heredoc (path/role/dept may contain
# quotes or spaces that would break inline python interpolation)
AC_CYCLE_FILE="$CYCLE_FILE" AC_KEY="$KEY" AC_ROLE="$ROLE" AC_DEPT="$DEPT" AC_TIMESTAMP="$TIMESTAMP" python3 - <<'PYEOF'
import json, os

f = os.environ['AC_CYCLE_FILE']
data = {}
if os.path.exists(f):
    try: data = json.load(open(f))
    except Exception: data = {}

key = os.environ['AC_KEY']
if key not in data:
    data[key] = {'role': os.environ['AC_ROLE'], 'department': os.environ['AC_DEPT'], 'cycles': 0, 'first_seen': os.environ['AC_TIMESTAMP'], 'last_seen': os.environ['AC_TIMESTAMP']}

data[key]['cycles'] += 1
data[key]['last_seen'] = os.environ['AC_TIMESTAMP']

json.dump(data, open(f, 'w'), indent=2)
print(data[key]['cycles'])
PYEOF
