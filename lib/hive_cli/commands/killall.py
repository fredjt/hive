"""hive killall — emergency stop for stuck hive processes."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


HIVE_ROOT = Path(os.environ.get("HIVE_ROOT", Path(__file__).resolve().parents[3]))


def handle(args) -> int:
    cmd = ["bash", str(HIVE_ROOT / "lib" / "tools" / "hive-killall.sh")]
    if args.dry_run:
        cmd.append("--dry-run")
    return subprocess.run(cmd).returncode
