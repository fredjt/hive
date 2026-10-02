#!/usr/bin/env bash
set -euo pipefail

# scaffold-extract.sh — deprecated.
# Projects now use the installed scripts directly from HIVE_INSTALL_DIR;
# no per-project .hive/ copy is created or needed.
# This script remains for backward compatibility and simply prints a notice.

echo "scaffold-extract: per-project .hive/ scaffolds are no longer used." >&2
echo "Projects now use installed scripts directly from \$HIVE_INSTALL_DIR." >&2
echo "If you see this message, remove the old .hive/ directory:" >&2
echo "  rm -rf <project>/.hive" >&2
exit 0
