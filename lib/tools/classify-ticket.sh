#!/usr/bin/env bash
set -euo pipefail

# classify-ticket.sh - Classify a free-text request into (type, department)
# using local keyword heuristics. No external model needed.
#
# Type classification (bug|feature|feedback):
#   - "bug": keywords like broken, error, crash, fail, bug, issue, not working,
#     doesn't work, regression, unexpected, wrong, incorrect, missing
#   - "feature": keywords like new, add, support, request, want, need, capability,
#     improve, enhance, create, implement, feature, option
#   - "feedback": everything else (opinions, comments, suggestions without action)
#
# Department classification:
#   - Matches keywords against department descriptions from org-chart.json
#   - Falls back to filesystem-discovered departments
#   - Uses keyword matching with weighted scoring
#
# Output: JSON  {"type":"bug|feature|feedback","department":"<name>","confidence":"high|low"}

if [ $# -lt 1 ]; then
  echo "Usage: classify-ticket.sh \"<request text>\"" >&2
  exit 1
fi

REQUEST="$1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPANY_DIR="${COMPANY_DIR:-.company}"

# --- Type classification via keyword heuristics ---
classify_type() {
  local req_lower
  req_lower=$(echo "$REQUEST" | tr '[:upper:]' '[:lower:]')

  # Bug keywords (check first - most specific)
  if echo "$req_lower" | grep -qiE '\b(broken|error|crash|fail(ed|ure)?|bug|issue|not working|doesnt work|regression|unexpected|wrong|incorrect|missing|defect|exception|traceback|stack.?trace)\b'; then
    echo "bug"
    return
  fi

  # Feature keywords
  if echo "$req_lower" | grep -qiE '\b(new|add(ed)?|support(s|ed)?|request|want(ed)?|need(ed|s)?|capability|improve(d|ment)?|enhance|create|implement|feature|option|allow|enable|provide|introduce)\b'; then
    echo "feature"
    return
  fi

  # Default to feedback for opinions, comments, suggestions without clear action
  echo "feedback"
}

# --- Department classification via keyword scoring ---
classify_department() {
  local req_lower
  req_lower=$(echo "$REQUEST" | tr '[:upper:]' '[:lower:]')

  # Baseline department keywords (always present)
  declare -A DEPT_KEYWORDS=(
    [hr]="hire fire performance review org structure manage employee staff team member interview onboarding offboarding policy benefit compensation salary training conflict grievance disciplinary promotion transfer"
    [it]="build ci cd dependency dev tooling server disk infrastructure deploy pipeline docker container kubernetes network firewall proxy cache storage backup monitor alert log telemetry metric dashboard uptime bandwidth cpu memory gpu volume mount filesystem partition"
    [rd]="research competitive analysis technology innovation prototype experiment proof concept benchmark compare evaluate assess study explore investigate discover novel algorithm model architecture framework library pattern method strategy roadmap vision future trend emerging advanced sophisticated"
    [marketing]="report status communication content blog post article newsletter email campaign social media website landing page seo branding brand identity visual design graphic illustration icon image photo video audio sound music podcast presentation slide deck pitch proposal document white paper case study testimonial review rating survey poll questionnaire form vote opinion comment suggestion idea voice customer user"
  )

  # Also pull departments from org-chart if available
  declare -A ORG_KEYWORDS=()
  if [ -f "$COMPANY_DIR/org-chart.json" ]; then
    while IFS=$'\t' read -r name keywords; do
      [ -z "$name" ] && continue
      case " ${!DEPT_KEYWORDS[*]} " in *" $name "*) continue ;; esac
      ORG_KEYWORDS[$name]="$keywords"
    done < <(jq -r '.departments[]? | [.name, (.description // .purpose // .focus // "")] | @tsv' \
             "$COMPANY_DIR/org-chart.json" 2>/dev/null || true)
  fi

  # Merge all departments and their keywords
  declare -A ALL_KEYWORDS=()
  for dept in "${!DEPT_KEYWORDS[@]}"; do
    ALL_KEYWORDS[$dept]="${DEPT_KEYWORDS[$dept]}"
  done
  for dept in "${!ORG_KEYWORDS[@]}"; do
    if [ -n "${ALL_KEYWORDS[$dept]:-}" ]; then
      ALL_KEYWORDS[$dept]="${ALL_KEYWORDS[$dept]} ${ORG_KEYWORDS[$dept]}"
    else
      ALL_KEYWORDS[$dept]="${ORG_KEYWORDS[$dept]}"
    fi
  done

  # Also discover departments from filesystem
  if [ -d "$COMPANY_DIR/departments" ]; then
    for dept_dir in "$COMPANY_DIR/departments"/*/; do
      [ -d "$dept_dir" ] || continue
      local name
      name="$(basename "$dept_dir")"
      if [ -z "${ALL_KEYWORDS[$name]:-}" ]; then
        local hint
        hint=$(echo "$name" | tr '[-_]' ' ' | sed 's/\b\.\)/\L\1/g')
        ALL_KEYWORDS[$name]="$hint work project task ticket"
      fi
    done
  fi

  # Score each department by keyword matches
  local best_dept="unknown"
  local best_score=0

  for dept in "${!ALL_KEYWORDS[@]}"; do
    local score=0
    for kw in ${ALL_KEYWORDS[$dept]}; do
      if echo "$req_lower" | grep -qw "$kw" 2>/dev/null; then
        score=$((score + 1))
      fi
    done
    if [ "$score" -gt "$best_score" ]; then
      best_score=$score
      best_dept="$dept"
    fi
  done

  echo "${best_dept:-unknown}"
}

TYPE=$(classify_type)
DEPT=$(classify_department)

# Confidence: low if department is unknown, otherwise high
CONFIDENCE="high"
[ "$DEPT" = "unknown" ] && CONFIDENCE="low"

jq -n --arg t "$TYPE" --arg d "$DEPT" --arg c "$CONFIDENCE" \
  '{type: $t, department: $d, confidence: $c}'
