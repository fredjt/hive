#!/usr/bin/env bash
set -euo pipefail

# report-write.sh - Write reports to appropriate directories.
#
# Usage:
#   report-write.sh <type> <filename> [--content "..." | --stdin]
#
# Types:
#   marketing                  -> .company/marketing/reports/<filename>
#   research                   -> .company/research/ongoing/<filename>
#   meeting                    -> .company/meetings/history/<filename>
#   productivity | hiring      -> .company/departments/hr/<filename>
#   dept:<dept-name>           -> .company/departments/<dept>/reports/<filename>
#
# Filename may include subdirectories (e.g. "2026-Q2/budget.md") — the parent
# directories are created automatically.
#
# Returns: Absolute path to written file.

if [ $# -lt 2 ]; then
  echo "Usage: report-write.sh <type> <filename> [--content \"...\" | --stdin]" >&2
  echo "Types: marketing, research, meeting, productivity, hiring, dept:<dept>" >&2
  exit 1
fi

TYPE="$1"
FILENAME="$2"
shift 2

CONTENT=""
USE_STDIN=false

# Parse content source
while [ $# -gt 0 ]; do
  case "$1" in
    --content)
      CONTENT="$2"
      shift 2
      ;;
    --stdin)
      USE_STDIN=true
      shift
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

# Determine COMPANY_DIR
COMPANY_DIR="${COMPANY_DIR:-.company}"

# Determine destination directory based on type.
#
# Two ways to specify a destination:
#   1. Named alias (marketing/research/meeting/sprint-review/productivity/
#      hiring/dept:<dept>) — recognized by both this script AND the
#      dashboard's Reports panel (which aggregates from these paths).
#   2. Path-like type (e.g. "meetings/sprint-reviews", "rd/findings",
#      "departments/hr/reports") — treated as a subdirectory of .company/.
#      Use this when none of the named aliases fit; the dashboard's
#      Reports panel scans a few specific roots so a path-like type may
#      not appear in the Reports tab unless it's under one of them.
case "$TYPE" in
  marketing)
    DEST_DIR="$COMPANY_DIR/marketing/reports"
    ;;
  research)
    DEST_DIR="$COMPANY_DIR/research/ongoing"
    ;;
  meeting)
    DEST_DIR="$COMPANY_DIR/meetings/history"
    ;;
  sprint-review|sprint-reviews)
    # End-of-sprint VP reports. The dashboard's Reports panel reads from
    # .company/meetings/sprint-reviews/ as of 0.4.56.
    DEST_DIR="$COMPANY_DIR/meetings/sprint-reviews"
    ;;
  productivity|hiring)
    # Route to hr/reports/ (not the dept root) so the dashboard's Reports
    # panel picks them up via its .../<dept>/reports/** scan. Pre-0.4.56
    # they went to .company/departments/hr/ which the dashboard ALSO
    # scans for top-level *.md, so both work — but reports/ is consistent
    # with how every other dept is handled.
    DEST_DIR="$COMPANY_DIR/departments/hr/reports"
    ;;
  dept:*)
    # dept:<dept-name> — write under that department's reports/ subdir
    dept="${TYPE#dept:}"
    if [ -z "$dept" ]; then
      echo "Error: dept:<name> requires a department name (got dept:)" >&2
      exit 1
    fi
    DEST_DIR="$COMPANY_DIR/departments/$dept/reports"
    ;;
  */*)
    # Path-like type: treat as a subdirectory of .company/. Reject anything
    # with .. or leading / to keep the write contained.
    case "$TYPE" in
      *..*|/*)
        echo "Error: report type '$TYPE' contains .. or leading / (refused)" >&2
        exit 1
        ;;
    esac
    DEST_DIR="$COMPANY_DIR/$TYPE"
    ;;
  *)
    echo "Error: Unknown report type: $TYPE" >&2
    echo "Valid types:" >&2
    echo "  marketing | research | meeting | sprint-review | productivity | hiring" >&2
    echo "  dept:<dept-name>          (writes to .company/departments/<dept>/reports/)" >&2
    echo "  <path/like/this>          (writes to .company/<path>/)" >&2
    exit 1
    ;;
esac

# Build the destination file path. Filename can include subdirectories
# (e.g. "2026-Q2/budget.md") — ensure the file's parent dir exists, not
# just the type-determined root. Old behavior: mkdir -p $DEST_DIR only,
# which caused the write to fail with "No such file or directory" when
# $FILENAME had any path component (the MCP tool callers hit this often).
REPORT_FILE="$DEST_DIR/$FILENAME"
mkdir -p "$(dirname "$REPORT_FILE")"

if [ "$USE_STDIN" = true ]; then
  cat > "$REPORT_FILE"
else
  echo "$CONTENT" > "$REPORT_FILE"
fi

# Output absolute path
realpath "$REPORT_FILE"
