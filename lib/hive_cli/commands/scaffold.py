"""hive scaffold — deprecated.

Per-project .hive/ scaffolds are no longer used. Projects now use the
installed scripts directly from $HIVE_INSTALL_DIR/lib/tools/. This command
remains for backward compatibility and simply prints a notice.
"""

from __future__ import annotations


def handle(args) -> int:
    print("scaffold: per-project .hive/ scaffolds are no longer used.", file=__import__("sys").stderr)
    print("Projects now use installed scripts directly from $HIVE_INSTALL_DIR/lib/tools/.", file=__import__("sys").stderr)
    print("If you see this message, remove the old .hive/ directory:", file=__import__("sys").stderr)
    print("  rm -rf <project>/.hive", file=__import__("sys").stderr)
    return 0
