#!/bin/bash
# Archives old outbox files to .company/outbox/archive/
# Usage: outbox-archive.sh
# Moves all outbox .md files to archive except user-questions.md and pending-approvals.md

COMPANY_DIR="${COMPANY_DIR:-.company}"
OUTBOX="$COMPANY_DIR/outbox"
ARCHIVE="$OUTBOX/archive"

mkdir -p "$ARCHIVE"

archived=0
for f in "$OUTBOX"/*.md; do
  [ -f "$f" ] || continue
  base=$(basename "$f")
  # Keep active files that agents write to
  case "$base" in
    pending-approvals.md|user-questions.md) continue ;;
  esac
  mv "$f" "$ARCHIVE/"
  archived=$((archived + 1))
done

# Clear the kept files if they exist
for f in pending-approvals.md user-questions.md; do
  if [ -f "$OUTBOX/$f" ]; then
    echo "" > "$OUTBOX/$f"
  fi
done

echo "Archived $archived outbox files to $ARCHIVE/"
