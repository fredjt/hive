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

python3 -c "
import json, os

f = '$CYCLE_FILE'
data = {}
if os.path.exists(f):
    try: data = json.load(open(f))
    except: data = {}

key = '$KEY'
if key not in data:
    data[key] = {'role': '$ROLE', 'department': '$DEPT', 'cycles': 0, 'first_seen': '$TIMESTAMP', 'last_seen': '$TIMESTAMP'}

data[key]['cycles'] += 1
data[key]['last_seen'] = '$TIMESTAMP'

json.dump(data, open(f, 'w'), indent=2)
print(data[key]['cycles'])
"
