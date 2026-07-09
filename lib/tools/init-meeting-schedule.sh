#!/usr/bin/env bash
set -euo pipefail

# init-meeting-schedule.sh - Create initial meeting schedule
# Usage: init-meeting-schedule.sh
# Returns: JSON on stdout

# Determine COMPANY_DIR
COMPANY_DIR="${COMPANY_DIR:-.company}"

# Create schedule JSON
cat > "$COMPANY_DIR/meetings/schedule.json" <<EOF
{
  "next_standup": null,
  "next_sprint_review": null,
  "next_sprint_planning": null,
  "last_standup": null,
  "last_sprint_review": null,
  "last_sprint_planning": null
}
EOF

# Output the created JSON to stdout
cat "$COMPANY_DIR/meetings/schedule.json"
