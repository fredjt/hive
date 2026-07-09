#!/usr/bin/env bash
# load-agent-prompt.sh — sourced by stage-enqueue scripts to assemble the
# spawn prompt. Concatenates the canonical framework primer with the
# role-specific .md file, so every spawn always sees up-to-date framework
# context regardless of how stale the role-specific prompt is.
#
# Usage (inside a stage-enqueue script):
#   . "$SKILL_DIR/lib/tools/load-agent-prompt.sh"
#   PROMPT="$(load_agent_prompt "$AGENT_FILE")"
#
# Returns the concatenation:
#   <_FRAMEWORK_PRIMER.md>
#   <agent role file>
#
# If the primer is missing, falls back to the role file alone (so the
# system keeps working during partial upgrades).

load_agent_prompt() {
  local role_file="$1"
  local primer="${SKILL_DIR:-${HIVE_ROOT:-$HOME/.local/share/hive}}/lib/agents/_FRAMEWORK_PRIMER.md"
  if [ -f "$primer" ]; then
    cat "$primer"
    echo
  fi
  cat "$role_file"
}
