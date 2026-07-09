"""hive init"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def handle(args) -> int:
    hive_root = Path(os.environ.get("HIVE_ROOT", Path(__file__).resolve().parents[3]))
    project_dir = str(Path(args.project_dir).resolve())
    cmd = ["bash", str(hive_root / "lib" / "core" / "init.sh"), project_dir]
    if args.brief:
        cmd += ["--brief", args.brief]
    if args.brief_file:
        cmd += ["--brief-file", args.brief_file]
    if args.auto:
        cmd += ["--auto"]
    if args.name:
        cmd += ["--name", args.name]
    if getattr(args, "github", None):
        cmd += ["--github", args.github]
    proc = subprocess.run(cmd, env={**os.environ, "HIVE_ROOT": str(hive_root)})
    return proc.returncode
