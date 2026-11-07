#!/bin/bash
# Usage: agent-cycle.sh <agent-role> <department>
# Increments per-agent cycle count and records timestamp.
# Agents call this at the start of each work cycle.
# Data stored in .company/logs/agent-cycles.json

COMPANY_DIR="${COMPANY_DIR:-.company}"
# shellcheck source=/dev/null
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/hive-state.sh"
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
# State layer: create the log if missing, then one locked atomic increment.
if ! hs --company-dir "$COMPANY_DIR" read "logs/agent-cycles.json" >/dev/null 2>&1; then
  hs --company-dir "$COMPANY_DIR" write "logs/agent-cycles.json" --json '{}' >/dev/null || true
fi
hs --company-dir "$COMPANY_DIR" jq "logs/agent-cycles.json" \
  --arg k "$KEY" --arg r "$ROLE" --arg d "$DEPT" --arg t "$TIMESTAMP" \
  '.[$k] = ((.[$k] // {role:$r, department:$d, cycles:0, first_seen:$t, last_seen:$t})
           | .cycles = ((.cycles // 0) + 1) | .last_seen = $t)' >/dev/null
hs --company-dir "$COMPANY_DIR" read "logs/agent-cycles.json" | jq -r ".[\"$KEY\"].cycles // 1"
