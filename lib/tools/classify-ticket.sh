#!/usr/bin/env bash
set -euo pipefail

# classify-ticket.sh - Use the small CPU model to classify a free-text request
# into (type, department) without burning a main-model turn.
#
# Builds rich prompts with department descriptions because tiny models (0.8B-3B)
# can't reliably pick from a bare list of names. Descriptions come from:
#   1. Hardcoded baseline (hr, it, rd, marketing - always present)
#   2. .company/org-chart.json description fields (dynamic depts, if present)
#   3. Fallback to dept name as its own "description"
#
# Output: JSON  {"type":"bug|feature|feedback","department":"<dept-name>","confidence":"high|low","source":"llm-tiny|fallback"}
# Falls back to {"type":"feature","department":"unknown",...} if the small model
# is unreachable. Callers can route to CEO inbox in that case.
#
# Usage: classify-ticket.sh "<free text describing the request>"

if [ $# -lt 1 ]; then
  echo "Usage: classify-ticket.sh \"<request text>\"" >&2
  exit 1
fi

REQUEST="$1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPANY_DIR="${COMPANY_DIR:-.company}"

fallback() {
  jq -n --arg t "feature" --arg d "unknown" \
    '{type: $t, department: $d, confidence: "low", source: "fallback"}'
}

# Baseline descriptions for the always-present departments
declare -A DEPT_DESCRIPTIONS=(
  [hr]="hiring, firing, performance reviews, org structure, agent management"
  [it]="build system, CI/CD, dependencies, dev tooling, server/disk infrastructure"
  [rd]="research, competitive analysis, new technology, innovation tickets"
  [marketing]="user-facing reports, status communications, content"
)

# Pull dynamic departments + descriptions from org-chart if available
DEPTS=()
for d in hr it rd marketing; do DEPTS+=("$d"); done

if [ -f "$COMPANY_DIR/org-chart.json" ]; then
  while IFS=$'\t' read -r name desc; do
    [ -z "$name" ] && continue
    # Skip if already in DEPTS (avoid duplicates)
    case " ${DEPTS[*]} " in *" $name "*) ;; *) DEPTS+=("$name") ;; esac
    [ -n "$desc" ] && DEPT_DESCRIPTIONS[$name]="$desc"
  done < <(jq -r '.departments[]? | [.name, (.description // .purpose // .focus // "")] | @tsv' \
             "$COMPANY_DIR/org-chart.json" 2>/dev/null)
fi

# Add filesystem-discovered departments without descriptions
if [ -d "$COMPANY_DIR/departments" ]; then
  for d in "$COMPANY_DIR/departments"/*/; do
    name="$(basename "$d")"
    case " ${DEPTS[*]} " in *" $name "*) ;; *) DEPTS+=("$name") ;; esac
  done
fi

# Provide a heuristic description for any dept without one (use the name itself
# split on hyphens/underscores as a hint)
for d in "${DEPTS[@]}"; do
  if [ -z "${DEPT_DESCRIPTIONS[$d]:-}" ]; then
    DEPT_DESCRIPTIONS[$d]="${d//-/, } work"
  fi
done

# Build the dept list and the description block
DEPT_LIST=$(IFS='|'; echo "${DEPTS[*]}|unknown")
DESC_BLOCK=""
for d in "${DEPTS[@]}"; do
  DESC_BLOCK+="- $d: ${DEPT_DESCRIPTIONS[$d]}"$'\n'
done
DESC_BLOCK+="- unknown: cannot determine which department"

# --- Type classification ---
TYPE=$(bash "$SCRIPT_DIR/llm-tiny.sh" --choice "bug|feature|feedback" \
  "Classify this user request as one of: bug (something is broken), feature (new capability requested), feedback (opinion/comment, no action needed).

Request: $REQUEST" 2>/dev/null) || {
  fallback
  exit 0
}

# --- Department classification ---
DEPT=$(bash "$SCRIPT_DIR/llm-tiny.sh" --choice "$DEPT_LIST" \
  "Pick the right department for the request below.

Departments:
$DESC_BLOCK

Request: $REQUEST" 2>/dev/null) || DEPT="unknown"

# Confidence: low if we got "unknown" for either, otherwise high
CONFIDENCE="high"
[ "$DEPT" = "unknown" ] && CONFIDENCE="low"

jq -n --arg t "$TYPE" --arg d "$DEPT" --arg c "$CONFIDENCE" \
  '{type: $t, department: $d, confidence: $c, source: "llm-tiny"}'
