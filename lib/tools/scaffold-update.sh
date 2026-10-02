#!/usr/bin/env bash
set -euo pipefail

# scaffold-update.sh — deprecated.
# Per-project .hive/ scaffolds are no longer used; projects use installed
# scripts directly from $HIVE_INSTALL_DIR/lib/tools/.
# This script remains for backward compatibility and simply prints a notice.

echo "scaffold-update: per-project .hive/ scaffolds are no longer used." >&2
echo "Projects now use installed scripts directly from \$HIVE_INSTALL_DIR/lib/tools/." >&2
exit 0
