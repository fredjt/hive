#!/usr/bin/env bash
set -euo pipefail

# hive hooks — PreToolUse hook registration for ~/.claude/settings.json.
#
# Sourced by packaging/install.sh (hive_hooks_register) and
# packaging/uninstall.sh (hive_hooks_unregister). Both hive PreToolUse hooks
# are registered under ONE canonical matcher — Write|Edit|NotebookEdit|Bash.
# Bash must be in the matcher: the bash branch of block-company-writes.sh is
# what blocks the `ln -s ... .company` self-symlink and `rm -rf .company`
# classes of incidents (pre-0.4.47 the matcher was Write|Edit only and Bash
# mutations bypassed the hook).
#
# Registration is idempotent and MIGRATING: any PreToolUse entry whose
# command references either hook script is removed first (this retires the
# legacy narrow "Write|Edit" registrations and any duplicates from older
# installs, whatever install dir they point at), then the canonical entry is
# added. Everything else in settings.json (other tools' hooks, permissions,
# env vars) is preserved untouched.
#
# Both functions are best-effort: a missing jq or an invalid settings.json
# prints a warning and returns 0 — install/uninstall must not fail over hook
# wiring, and an unparseable user file is never clobbered.

HIVE_HOOK_MATCHER='Write|Edit|NotebookEdit|Bash'

# jq filter: drop every PreToolUse entry whose hooks reference a hive hook
# script (matched on the script basename so entries from OLD install dirs are
# retired too), then drop entries that are left with no hooks.
_hive_hooks_strip_filter='
.hooks //= {}
| .hooks.PreToolUse = ((.hooks.PreToolUse // [])
    | map(
        .hooks |= ((. // [])
            | map(select(
                ((.command // "") | test("block-company-writes\\.sh") | not)
                and ((.command // "") | test("block-admin-code-writes\\.sh") | not)
              ))
        )
        | select((.hooks // []) | length > 0)
      ))
'

_hive_settings_file() {
  echo "${HOME}/.claude/settings.json"
}

# Shared preamble: returns 0 if it is safe to touch the settings file,
# 1 if we must back off (missing jq / invalid JSON).
_hive_hooks_preflight() {
  if ! command -v jq >/dev/null 2>&1; then
    echo "  WARNING: jq not found — skipping PreToolUse hook wiring. Install jq and re-run to enable the .company/ write-protection hooks." >&2
    return 1
  fi
  local settings
  settings="$(_hive_settings_file)"
  mkdir -p "$(dirname "$settings")" 2>/dev/null || true
  if [ -f "$settings" ]; then
    if ! jq empty "$settings" >/dev/null 2>&1; then
      echo "  WARNING: $settings is not valid JSON — leaving it untouched (fix it manually, then re-run install). Hook protection is NOT active until that file parses." >&2
      return 1
    fi
  else
    echo '{}' > "$settings"
  fi
  return 0
}

hive_hooks_register() {
  local install_dir="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
  local company_hook admin_hook settings tmp
  company_hook="bash $install_dir/lib/core/block-company-writes.sh"
  admin_hook="bash $install_dir/lib/core/block-admin-code-writes.sh"

  if ! _hive_hooks_preflight; then
    return 0
  fi
  settings="$(_hive_settings_file)"

  # 1. Strip any pre-existing hive registrations (legacy narrow matchers,
  #    duplicates, stale install-dir paths).
  tmp="${settings}.tmp.$$"
  if ! jq "$_hive_hooks_strip_filter" "$settings" > "$tmp"; then
    rm -f "$tmp"
    echo "  WARNING: failed to update $settings — hook registration aborted (file left untouched). Hook protection is NOT active." >&2
    return 0
  fi
  mv "$tmp" "$settings"

  # 2. Add the canonical entry. Post-strip it cannot exist yet, so this is
  #    idempotent across re-installs.
  tmp="${settings}.tmp.$$"
  if ! jq --arg m "$HIVE_HOOK_MATCHER" --arg c1 "$company_hook" --arg c2 "$admin_hook" '
      .hooks //= {}
      | .hooks.PreToolUse //= []
      | .hooks.PreToolUse += [{
          matcher: $m,
          hooks: [
            {type: "command", command: $c1, timeout: 5},
            {type: "command", command: $c2, timeout: 5}
          ]
        }]
    ' "$settings" > "$tmp"; then
    rm -f "$tmp"
    echo "  WARNING: failed to update $settings — hook registration aborted (file left untouched). Hook protection is NOT active." >&2
    return 0
  fi
  mv "$tmp" "$settings"
  echo "  Registered PreToolUse hooks ($HIVE_HOOK_MATCHER): block-company-writes.sh, block-admin-code-writes.sh"

  # 3. Verify — if the check fails, the write did not land; report it
  #    instead of silently continuing without the safety property the agent
  #    prompts promise.
  if ! jq -e --arg n "block-company-writes.sh" '
      [.hooks.PreToolUse[]?
        | select((.matcher // "") | test("Bash"))
        | (.hooks // [])[]
        | select((.command // "") | contains($n))
      ] | length > 0
    ' "$settings" >/dev/null 2>&1; then
    echo "  ERROR: hook registration did not verify — $settings does not contain a Bash-matching registration for block-company-writes.sh. Agents are NOT write-protected; check the file and re-run install.sh." >&2
    return 1
  fi
  return 0
}

hive_hooks_unregister() {
  if ! _hive_hooks_preflight; then
    return 0
  fi
  local settings tmp
  settings="$(_hive_settings_file)"

  # Nothing to do if no hive entry is present.
  if ! jq -e '[(.hooks.PreToolUse // [])[]
        | (.hooks // [])[]
        | (.command // "")]
        | map(select(test("block-(company|admin-code)-writes\\.sh")))
        | length > 0' "$settings" >/dev/null 2>&1; then
    return 0
  fi

  tmp="${settings}.tmp.$$"
  if ! jq "$_hive_hooks_strip_filter" "$settings" > "$tmp"; then
    rm -f "$tmp"
    echo "  WARNING: failed to update $settings — hook removal aborted (file left untouched)." >&2
    return 0
  fi
  mv "$tmp" "$settings"
  echo "  Removed PreToolUse hook registrations from $settings"
  return 0
}
