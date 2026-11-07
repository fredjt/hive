#!/usr/bin/env bash
# load-agent-prompt.sh — sourced by stage-enqueue scripts to assemble the
# spawn prompt. Concatenates the canonical framework primer with the
# role-specific .md file, so every spawn always sees up-to-date framework
# context regardless of how stale the role-specific prompt is.
#
# Usage (inside a stage-enqueue script):
#   . "$SKILL_DIR/lib/tools/load-agent-prompt.sh"
#   PROMPT="$(load_agent_prompt "$AGENT_FILE")"
#   PROMPT="$(load_dept_vp_prompt "$dept")"   # fixed-six VPs (G7: composed)
#
# load_agent_prompt returns the concatenation:
#   <_FRAMEWORK_PRIMER.md>
#   <agent role file>
#
# load_dept_vp_prompt returns the concatenation:
#   <_FRAMEWORK_PRIMER.md>
#   <composed dept VP prompt>
# where the composed prompt is rendered by lib/hive_agents.py (base
# vp-dept.md + the vp-<dept>.md fragment; dynamic depts get the base).
#
# If the primer is missing, falls back to the role content alone (so the
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

# Render the composed dept VP prompt (G7). The composition logic lives in
# lib/hive_agents.py — this wrapper only adds the primer, so the Python
# side is the single implementation (same pattern as config-get.sh/G5).
load_dept_vp_prompt() {
  local dept="$1"
  local root="${SKILL_DIR:-${HIVE_ROOT:-$HOME/.local/share/hive}}"
  local lib_dir="$root/lib"
  local primer="$lib_dir/agents/_FRAMEWORK_PRIMER.md"
  local py="${HIVE_AGENTS_PYTHON:-}"
  if [ -z "$py" ] && [ -x "$root/venv/bin/python" ]; then
    py="$root/venv/bin/python"
  fi
  if [ -z "$py" ]; then
    py="$(command -v python3 || command -v python || true)"
  fi
  if [ -z "$py" ]; then
    echo "load_dept_vp_prompt: no python found" >&2
    return 1
  fi
  if [ -f "$primer" ]; then
    cat "$primer"
    echo
  fi
  PYTHONPATH="$lib_dir" "$py" -c \
    'import sys, hive_agents; sys.stdout.write(hive_agents.compose_dept_vp(sys.argv[1]))' "$dept"
}
