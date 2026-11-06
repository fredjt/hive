#!/usr/bin/env bash
set -euo pipefail

# init-workspace.sh - Create full .company/ directory structure
# Usage: init-workspace.sh <project-root>
# Returns: 0 on success, exits with error message on failure

if [ $# -lt 1 ]; then
  echo "Usage: init-workspace.sh <project-root>" >&2
  exit 1
fi

PROJECT_ROOT="$1"
COMPANY_DIR="$PROJECT_ROOT/.company"

# Validate project root exists
if [ ! -d "$PROJECT_ROOT" ]; then
  echo "Error: Project root does not exist: $PROJECT_ROOT" >&2
  exit 1
fi

# Check if .company already exists — still create missing subdirs for idempotency
if [ ! -d "$COMPANY_DIR" ]; then
  echo "Creating .company/ workspace at $COMPANY_DIR..." >&2
fi

# Core directories
mkdir -p "$COMPANY_DIR/departments/hr"
mkdir -p "$COMPANY_DIR/departments/it"
mkdir -p "$COMPANY_DIR/departments/rd"
mkdir -p "$COMPANY_DIR/departments/marketing"
mkdir -p "$COMPANY_DIR/research/ongoing"
mkdir -p "$COMPANY_DIR/integration"
mkdir -p "$COMPANY_DIR/qa"
mkdir -p "$COMPANY_DIR/reports"
mkdir -p "$COMPANY_DIR/tickets"

# Perpetual operations directories
mkdir -p "$COMPANY_DIR/chat"
mkdir -p "$COMPANY_DIR/logs"
mkdir -p "$COMPANY_DIR/marketing/reports"
mkdir -p "$COMPANY_DIR/marketing/output"
mkdir -p "$COMPANY_DIR/marketing/media-research"

# Ensure .company and .worktrees are gitignored. NOTE: NO trailing slash —
# trailing-slash patterns (.company/) only match directories, NOT symlinks.
# Each worktree's .company is a symlink back to the project root's .company,
# and `git add -A` would stage that symlink despite ".company/" being in
# gitignore. Once the symlink lands in main's tree, every git checkout
# restores it as a self-loop and the workspace is bricked. Plain ".company"
# (no slash) ignores both directories and symlinks.
#
# Idempotent: only adds entries that aren't already present. ALSO upgrades
# any pre-existing trailing-slash form to the no-slash form.
GITIGNORE="$PROJECT_ROOT/.gitignore"
ensure_gitignore_entry() {
  local entry="$1" comment="$2"
  if [ -f "$GITIGNORE" ]; then
    # Upgrade ".company/" → ".company" if found (and same for .worktrees/)
    if grep -qxF "${entry}/" "$GITIGNORE" 2>/dev/null && ! grep -qxF "$entry" "$GITIGNORE" 2>/dev/null; then
      sed -i "s|^${entry}/$|${entry}|" "$GITIGNORE"
      echo "Upgraded ${entry}/ → ${entry} in .gitignore (trailing slash matches dirs only, not symlinks)" >&2
      return
    fi
  fi
  if [ ! -f "$GITIGNORE" ] || ! grep -qxF "$entry" "$GITIGNORE"; then
    {
      [ -f "$GITIGNORE" ] && echo ""
      [ -n "$comment" ] && echo "# $comment"
      echo "$entry"
    } >> "$GITIGNORE"
    echo "Added $entry to .gitignore" >&2
  fi
}
ensure_gitignore_entry ".company"   "Company skill workspace (state, logs, agent coordination)"
ensure_gitignore_entry ".worktrees" "Per-ticket git worktrees"


# Initialize empty JSON files from templates
echo '{"next_ticket_id": 1}' > "$COMPANY_DIR/tickets/global-counter.json"
echo '{"jobs": []}' > "$COMPANY_DIR/cron-jobs.json"

# Verification gates config — defaults to RELAXED so a fresh project can ship
# work without test infrastructure already in place. Tighten as the project
# matures by editing this file (see lib/tools/verification-gates.sh).
cat > "$COMPANY_DIR/gates.json" <<'EOF'
{
  "_doc": "Set true for any check that should BLOCK promotion to done. Defaults are relaxed for early-stage projects.",
  "mode": "strict",
  "require_tests":    true,
  "require_lint":     true,
  "require_security": true,
  "require_commits":  true,
  "coverage_min":     0
}
EOF

# Roster is derived - generate the initial (empty) version via roster-rebuild
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPANY_DIR="$COMPANY_DIR" bash "$SCRIPT_DIR/roster-rebuild.sh" --quiet 2>/dev/null || \
  echo '{"agents": [], "source": "roster-rebuild.sh", "note": "DERIVED FILE. Do not edit."}' > "$COMPANY_DIR/roster.json"

# Org chart is derived too — but E2: it must be rebuilt AFTER the manifest
# exists, or the compose stamps "Untitled Project" (and a manifest-less
# rebuild must not clobber a real name). init.sh rebuilds it right after
# init-manifest.sh; a stand-alone init-workspace run leaves any existing
# org-chart.json in place (the lifecycle/dashboard rebuild it on demand).

echo "Workspace created successfully at $COMPANY_DIR" >&2
exit 0
