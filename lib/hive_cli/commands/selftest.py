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


def venv_python_executable(p: Path) -> bool:
    """The Python-side equivalent of agent-spawn.sh's `[ -x file ]` test
    (pathlib has no is_executable)."""
    return p.is_file() and os.access(str(p), os.X_OK)


def install_root() -> Path:
    """The install root the spawn path uses (agent-spawn.sh's
    INSTALL_DIR): HIVE_INSTALL_DIR when set and present, else the root
    this CLI runs from (the same contract as the Python-side defaults).
    The venv lives here."""
    install_env = os.environ.get("HIVE_INSTALL_DIR")
    if install_env and Path(install_env).is_dir():
        return Path(install_env)
    return Path(__file__).resolve().parents[3]


def spawn_python(root: Path | None = None) -> str:
    """The interpreter agent-spawn.sh runs the harness under: the install
    venv's python when present AND executable (its `[ -x ... ]` test),
    else the current interpreter. `hive selftest` and `hive doctor` both
    probe with it, so the probe IS the spawn path — never a sibling
    interpreter that would pass where a real spawn dies."""
    root = root or install_root()
    venv_py = root / "venv" / "bin" / "python"
    return str(venv_py) if venv_python_executable(venv_py) else sys.executable


def handle(args) -> int:
    project = Path(getattr(args, "project_dir", ".")).resolve()
    root = install_root()
    py = spawn_python(root)
    harness = root / "lib" / "core" / "agent-harness.py"
    if not harness.is_file():
        print(f"selftest: agent harness not found at {harness}", file=sys.stderr)
        return 1
    env = {**os.environ, "HIVE_ROOT": str(root)}
    return subprocess.run([py, str(harness), "--selftest"],
                          env=env, cwd=str(project)).returncode
