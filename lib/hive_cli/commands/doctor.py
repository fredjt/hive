"""hive doctor — check that everything is wired up."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from hive_cli import mcp_client


# HIVE_INSTALL_DIR — global install (venv, MCP/CLI/dashboard source).
HIVE_INSTALL_DIR = Path(os.environ.get("HIVE_INSTALL_DIR",
                                       Path(__file__).resolve().parents[3]))
# HIVE_ROOT — per-project scaffold (set by bin/hive when sentinel found).
HIVE_ROOT = Path(os.environ.get("HIVE_ROOT", HIVE_INSTALL_DIR))


def _check(name: str, ok: bool, detail: str = "") -> bool:
    sym = "✓" if ok else "✗"
    color = "\033[32m" if ok else "\033[31m"
    print(f" {color}{sym}\033[0m {name:<35} {detail}")
    return ok


def handle(args) -> int:
    print("Hive doctor — checking installation\n")
    all_ok = True

    # Install dir (global) and scaffold (per-project, may equal install in dev).
    all_ok &= _check("HIVE_INSTALL_DIR exists", HIVE_INSTALL_DIR.is_dir(),
                     str(HIVE_INSTALL_DIR))
    all_ok &= _check("HIVE_ROOT (scaffold)", HIVE_ROOT.is_dir(), str(HIVE_ROOT))

    # Install-dir subdirs (where venv + source live).
    for sub in ("bin", "lib/hive_mcp", "lib/hive_cli", "lib/dashboard"):
        p = HIVE_INSTALL_DIR / sub
        all_ok &= _check(f"  install/{sub}/", p.is_dir())
    # Scaffold subdirs (per-project; in dev mode same as install dir).
    for sub in ("lib/tools", "lib/agents", "lib/core"):
        p = HIVE_ROOT / sub
        all_ok &= _check(f"  scaffold/{sub}/", p.is_dir())

    # Python venv (always from install dir).
    venv_py = HIVE_INSTALL_DIR / "venv" / "bin" / "python"
    all_ok &= _check("Python venv", venv_py.is_file(), str(venv_py))

    if venv_py.is_file():
        # tomllib is stdlib on Python 3.11+; fall back to tomli on older versions
        check_code = (
            "import mcp; import fastapi; import httpx; "
            "import sys; "
            "(__import__('tomllib') if sys.version_info >= (3,11) else __import__('tomli'))"
        )
        try:
            r = subprocess.run([str(venv_py), "-c", check_code],
                               capture_output=True, text=True)
            all_ok &= _check("Required Python packages", r.returncode == 0,
                             r.stderr.strip().splitlines()[-1] if r.returncode != 0 else "mcp, fastapi, httpx, tomllib/tomli")
        except Exception as e:
            all_ok &= _check("Required Python packages", False, str(e))

    # CLI
    all_ok &= _check("hive on PATH", shutil.which("hive") is not None)

    # External tools
    # Agent harness: Python-based, no external binary needed
    harness_path = HIVE_ROOT / "lib" / "core" / "agent-harness.py"
    all_ok &= _check("agent harness", harness_path.is_file(),
                     f"{harness_path} (Python-based, no CLI required)")
    all_ok &= _check("git", shutil.which("git") is not None)
    all_ok &= _check("jq", shutil.which("jq") is not None)
    all_ok &= _check("node (for dashboard)", shutil.which("node") is not None)
    all_ok &= _check("bash", shutil.which("bash") is not None)

    # MCP server reachable?
    print("\nMCP server:")
    tools = []
    try:
        tools = mcp_client.list_tools()
        all_ok &= _check("MCP server tools", len(tools) > 0, f"{len(tools)} tools registered")
    except Exception as e:
        all_ok &= _check("MCP server", False, str(e))

    # Core scripts present
    print("\nCore scripts:")
    for s in ("agent-spawn.sh", "agent-harness.py", "init.sh", "orchestrator.sh"):
        p = HIVE_ROOT / "lib" / "core" / s
        all_ok &= _check(f"  {s}", p.is_file())

    # Sample tool scripts
    print("\nSample tool scripts:")
    for s in ("ticket-create.sh", "verification-gates.sh", "ticket-approve.sh",
              "roster-rebuild.sh", "status-rebuild.sh", "company-start.sh"):
        p = HIVE_ROOT / "lib" / "tools" / s
        all_ok &= _check(f"  {s}", p.is_file())

    print()
    if all_ok:
        print("\033[32mAll checks passed.\033[0m")
        return 0
    print("\033[31mSome checks failed. See output above.\033[0m")
    return 1
