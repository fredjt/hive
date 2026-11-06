#!/usr/bin/env bash
set -euo pipefail

# org-chart-rebuild.sh - Derive .company/org-chart.json from filesystem state.
#
# Same pattern as roster-rebuild.sh + status-rebuild.sh: instead of relying on
# the CEO to write org-chart.json on initial build, we derive it from what's
# actually on disk. This means:
#   - Fresh projects get a valid org-chart.json (with the 4 fixed depts)
#   - As dynamic departments get created (mkdir + tickets/), they appear here
#   - The dashboard's Org tab always has something to render
#
# If the CEO has previously written a richer org-chart.json (with components,
# waves, etc.), we MERGE - preserving CEO-authored fields, only filling in
# departments that aren't already documented. This way the CEO's planning
# isn't overwritten.
#
# Usage: org-chart-rebuild.sh [--quiet]

QUIET=false
[ "${1:-}" = "--quiet" ] && QUIET=true

COMPANY_DIR="${COMPANY_DIR:-.company}"
ORG_FILE="$COMPANY_DIR/org-chart.json"
MANIFEST="$COMPANY_DIR/manifest.json"

[ -d "$COMPANY_DIR" ] || { echo "No .company at $COMPANY_DIR" >&2; exit 0; }

# Get project name from manifest if available.
# E2: the manifest WINS over the existing org-chart stamp — an init-time
# "Untitled Project" (stamped when the org chart was rebuilt before the
# manifest existed) must not survive forever. Without a manifest we keep
# the existing name rather than clobbering it with the placeholder.
PROJECT_NAME=""
if [ -f "$MANIFEST" ]; then
  PROJECT_NAME=$(jq -r '.project_name // empty' "$MANIFEST" 2>/dev/null)
fi

# Discover departments from filesystem (everything under .company/departments/).
# Use a regular array, not associative, so we can iterate even when empty.
DISCOVERED=()
if [ -d "$COMPANY_DIR/departments" ]; then
  for d in "$COMPANY_DIR/departments"/*/; do
    [ -d "$d" ] || continue
    DISCOVERED+=("$(basename "$d")")
  done
fi

# Read existing org-chart.json if present (so we can preserve CEO-authored data)
EXISTING="{}"
if [ -f "$ORG_FILE" ]; then
  if jq empty "$ORG_FILE" 2>/dev/null; then
    EXISTING=$(cat "$ORG_FILE")
  fi
fi

# Hardcoded fixed-department metadata (matches what CEO would write)
FIXED_META=$(cat <<'EOF'
{
  "hr":         {"type":"fixed", "vp":"VP-HR",        "purpose":"Hiring, performance, org structure, agent management"},
  "it":         {"type":"fixed", "vp":"VP-IT",        "purpose":"Build system, CI/CD, dependencies, dev tooling, infra"},
  "rd":         {"type":"fixed", "vp":"VP-R&D",       "purpose":"Research, competitive analysis, new technology"},
  "marketing":  {"type":"fixed", "vp":"VP-Marketing", "purpose":"Positioning, pricing, GTM, content, customer acquisition"},
  "legal":      {"type":"fixed", "vp":"VP-Legal",     "purpose":"Privacy, ToS, license compliance, risk register, IP, regulatory posture"},
  "finance":    {"type":"fixed", "vp":"VP-Finance",   "purpose":"Budget, runway, unit economics, projections, vendor cost tracking, P&L"}
}
EOF
)

# Build a JSON array of discovered department names. Tolerates empty DISCOVERED.
if [ ${#DISCOVERED[@]} -gt 0 ]; then
  DISC_JSON=$(printf '%s\n' "${DISCOVERED[@]}" | jq -R . | jq -s .)
else
  DISC_JSON='[]'
fi

# Build the full department list as JSON, ensuring fixed depts always come first
# and dynamic depts are filled in for anything in DISCOVERED that isn't already documented.
DEPTS_JSON=$(jq -n \
  --argjson existing "$EXISTING" \
  --argjson fixed_meta "$FIXED_META" \
  --argjson discovered "$DISC_JSON" \
  '
  # Start with existing departments (preserve CEO-authored fields)
  ($existing.departments // {}) as $existing_depts |

  # Build map: every fixed dept gets fixed_meta unless existing has data, every discovered dept gets a stub
  # Need ([])[] to iterate items rather than binding the whole array to $name; |unique to dedupe.
  reduce (($discovered + ["hr","it","rd","marketing","legal","finance"]) | unique | .[]) as $name ({};
    if $existing_depts[$name] then
      .[$name] = $existing_depts[$name]
    elif $fixed_meta[$name] then
      .[$name] = $fixed_meta[$name]
    else
      .[$name] = {
        "type": "dynamic",
        "vp": ("VP-" + ($name | gsub("[-_]"; " ") | split(" ") | map(.[:1] | ascii_upcase) | join(""))),
        "purpose": ($name | gsub("[-_]"; " ") + " department (auto-discovered from filesystem)"),
        "depth": "vp-ic",
        "managers": 0,
        "components": [],
        "dependencies": [],
        "wave": null,
        "auto_discovered": true
      }
    end
  )
  ')

# Compose the final org-chart.json (preserving company_name, project_name, waves, etc. from existing)
TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
jq -n \
  --arg ts "$TS" \
  --arg pname "$PROJECT_NAME" \
  --argjson existing "$EXISTING" \
  --argjson depts "$DEPTS_JSON" \
  '
  ($existing // {}) as $e |
  ($pname | if . == "" then null else . end) as $mname |
  {
    company_name:  ($e.company_name // ((($mname // "Untitled Project")) + " Co.")),
    project_name:  ($mname // $e.project_name // "Untitled Project"),
    departments:   $depts,
    waves:         ($e.waves // null),
    total_components:      ($e.total_components // ($depts | [.[].components // []] | add | length)),
    total_agents_estimate: ($e.total_agents_estimate // null),
    updated_at: $ts,
    source: "org-chart-rebuild.sh",
    note: "DERIVED FILE. Filesystem-discovered departments are filled in automatically; CEO-authored departments are preserved."
  } | with_entries(select(.value != null))
  ' > "$ORG_FILE.tmp"

mv "$ORG_FILE.tmp" "$ORG_FILE"

if [ "$QUIET" = false ]; then
  count=$(jq '.departments | length' "$ORG_FILE")
  fixed=$(jq '[.departments[] | select(.type=="fixed")] | length' "$ORG_FILE")
  dynamic=$(jq '[.departments[] | select(.type=="dynamic")] | length' "$ORG_FILE")
  echo "Org chart rebuilt: $count departments (${fixed} fixed, ${dynamic} dynamic)" >&2
fi
