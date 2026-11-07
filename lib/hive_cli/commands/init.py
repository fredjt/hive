"""hive init"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def handle(args) -> int:
    from hive_paths import install_root
    hive_root = install_root()
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
    if proc.returncode != 0:
        return proc.returncode
    # F2: preflight hint — if the resolved LLM is unconfigured, print the
    # exact fix (config file path + 'hive config edit') at the end instead
    # of letting the first spawn fail on it. A hint, not a gate: init
    # succeeded, so the exit code stays 0 — doctor is the gate.
    hint = _llm_unconfigured_hint(hive_root)
    if hint:
        print("")
        print("LLM preflight: " + hint)
        print("(hint only — init succeeded; run 'hive doctor' to verify)")
    return 0


def _llm_unconfigured_hint(hive_root: Path) -> str | None:
    """F2: the canonical unconfigured-LLM message (same one doctor and
    `hive llm test` use) when the resolved LLM is not configured, else
    None. Resolution errors are swallowed — the hint must never break an
    otherwise successful init."""
    import sys
    core_dir = str(hive_root / "lib" / "core")
    if core_dir not in sys.path:
        sys.path.insert(0, core_dir)
    try:
        from llm_providers import resolve, unconfigured_hint
        res = resolve(hive_root=hive_root)
    except Exception:
        return None
    if res.is_configured:
        return None
    return unconfigured_hint(hive_root=hive_root)
