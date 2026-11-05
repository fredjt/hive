"""hive selftest — the agent spawn path, probed for real (A2).

Execs agent-harness.py --selftest with the same interpreter agent-spawn.sh
would pick (the install venv python when present, otherwise the current
one) — so the probe IS the spawn path. Never calls the LLM.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def handle(args) -> int:
    project = Path(getattr(args, "project_dir", ".")).resolve()
    _fallback = Path(__file__).resolve().parents[3]
    install_env = os.environ.get("HIVE_INSTALL_DIR")
    if install_env and Path(install_env).is_dir():
        root = Path(install_env)
    else:
        root = _fallback
    venv_py = root / "venv" / "bin" / "python"
    py = str(venv_py) if venv_py.is_file() else sys.executable
    harness = root / "lib" / "core" / "agent-harness.py"
    if not harness.is_file():
        print(f"selftest: agent harness not found at {harness}", file=sys.stderr)
        return 1
    env = {**os.environ, "HIVE_ROOT": str(root)}
    return subprocess.run([py, str(harness), "--selftest"],
                          env=env, cwd=str(project)).returncode
