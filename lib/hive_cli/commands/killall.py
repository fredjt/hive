"""hive killall — emergency stop for stuck hive processes."""

from __future__ import annotations

import subprocess


from hive_paths import install_root

HIVE_ROOT = install_root()


def handle(args) -> int:
    cmd = ["bash", str(HIVE_ROOT / "lib" / "tools" / "hive-killall.sh")]
    if args.dry_run:
        cmd.append("--dry-run")
    return subprocess.run(cmd).returncode
