#!/usr/bin/env bash
set -euo pipefail

# migrate-agents.sh - Update remaining agent prompts to use scripts
# This handles agents with minimal .company/ write operations

AGENTS_DIR="$HOME/.claude/skills/company/agents"

echo "Migrating remaining agents to use scripts..."

# CEO - handles meeting records
if grep -q "Write.*meeting" "$AGENTS_DIR/ceo.md" 2>/dev/null; then
  echo "  - Updating ceo.md (meeting records)"
  sed -i 's|Write.*to.*\.company/meetings/history/|Use meeting-record.sh script to write meeting notes|g' "$AGENTS_DIR/ceo.md"
fi

# QA - handles QA reports
if grep -q "\.company/qa/results" "$AGENTS_DIR/qa.md" 2>/dev/null; then
  echo "  - Updating qa.md (QA reports)"
  sed -i 's|###.*\.company/qa/results-<focus-area>\.md|Use report-write.sh for QA reports:\n```bash\ncat <<EOF | bash SKILL_DIR/lib/tools/report-write.sh marketing results-<focus-area>.md --stdin\n# QA Results|g' "$AGENTS_DIR/qa.md"
fi

# Tech Lead - handles integration results
if grep -q "\.company/integration/results" "$AGENTS_DIR/tech-lead.md" 2>/dev/null; then
  echo "  - Updating tech-lead.md (integration results)"
  # Just add a note - tech-lead writes to project files mostly
  sed -i 's|###.*\.company/integration/results\.md|Use report-write.sh for integration results (or direct Write for project files)|g' "$AGENTS_DIR/tech-lead.md"
fi

echo "Migration complete for agents with .company/ writes"
echo ""
echo "Agents requiring manual review:"
echo "  - ic-engineer.md (writes project code, not .company/)"
echo "  - ic-infra.md (writes project code, not .company/)"
echo "  - manager.md (writes project code, not .company/)"
echo "  - marketing-*.md (may write to .company/marketing/)"
echo "  - verification-manager.md (delegates to IC-Testers)"
echo ""
echo "Note: Most IC and Manager agents write project code (outside .company/)"
echo "which is ALLOWED - only .company/ writes need script abstraction."
