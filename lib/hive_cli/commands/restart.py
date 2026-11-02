"""hive restart — restart the web dashboard without disturbing agents.

Kills only the existing dashboard (company-board) process, waits for the port
to free, then starts a fresh dashboard server.js.  The MCP server,
sprint-lifecycle, and all background daemons are left running.

Usage:
    hive restart <project-dir>     # restart dashboard for project
    hive restart                   # restart dashboard for CWD
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx


# HIVE_INSTALL_DIR — global install (venv, MCP server, dashboard).
HIVE_INSTALL_DIR = Path(os.environ.get("HIVE_INSTALL_DIR",
                                       Path(__file__).resolve().parents[3]))

# HIVE_ROOT — global install (projects use installed scripts directly).
HIVE_ROOT = Path(os.environ.get("HIVE_ROOT", HIVE_INSTALL_DIR))


def _find_dashboard_pid(company_dir: Path) -> int | None:
    """Return the PID of the company-board process from processes.json, or None."""
    pf = company_dir / "processes.json"
    if not pf.is_file():
        return None
    try:
        data = json.loads(pf.read_text())
        for p in data.get("processes", []):
            if p.get("role") == "company-board":
                return p.get("pid")
    except Exception:
        pass
    return None


def _kill_pid(pid: int) -> bool:
    """Send SIGTERM to *pid*; return True if it was alive."""
    try:
        os.kill(pid, signal.SIGTERM)
        return True
    except (ProcessLookupError, OSError):
        return False


def _wait_for_port(host: str, port: int, timeout: float = 10.0) -> bool:
    """Return True once the port is available (no listener)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            httpx.get(f"http://{host}:{port}/", timeout=0.5)
        except httpx.RequestError:
            return True  # port is free
        time.sleep(0.2)
    return False


def handle(args) -> int:
    project_dir = Path(args.project_dir).resolve()
    company_dir = project_dir / ".company"
    dash_port = args.port

    # 0. A restart is only meaningful if a dashboard was recorded. No
    #    company-board entry in processes.json means the company was never
    #    started (or no dashboard was launched) — starting one anyway would
    #    leave a half-started state, so fail cleanly instead.
    pid = _find_dashboard_pid(company_dir)
    if not pid:
        print(
            f"ERROR: no dashboard recorded in {company_dir / 'processes.json'} — "
            "nothing to restart. Start the company first (hive start); "
            "to launch only the dashboard use: hive dashboard",
            file=sys.stderr,
        )
        return 1

    # 1. Find and kill the existing dashboard (the recorded pid may already
    #    be dead — in that case skip the kill and start a fresh one).
    if _kill_pid(pid):
        print(f"Stopping dashboard (PID={pid}) ...")
        # Wait for it to exit + port to free.
        for _ in range(50):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            except OSError:
                break
            time.sleep(0.2)
        else:
            # Still alive — SIGKILL.
            try:
                os.kill(pid, signal.SIGKILL)
            except Exception:
                pass
            time.sleep(0.5)

    # 2. Wait for the port to be free (in case a stale process held it).
    if not _wait_for_port("127.0.0.1", dash_port, timeout=5):
        print(f"ERROR: port {dash_port} still in use. Is another dashboard running?",
              file=sys.stderr)
        return 1

    # 3. Start a new dashboard server.js.
    if shutil.which("node") is None:
        print("ERROR: node is required for the dashboard but not on PATH.",
              file=sys.stderr)
        return 1

    dash_log = company_dir / "logs" / "dashboard.log"
    company_dir.mkdir(parents=True, exist_ok=True)
    dash_log_f = open(dash_log, "ab")

    mcp_host = os.environ.get("HIVE_MCP_HOST", "127.0.0.1")
    mcp_port = int(os.environ.get("HIVE_MCP_PORT", "7891"))

    print(f"Starting dashboard on http://localhost:{dash_port}/ ...")
    dash_proc = subprocess.Popen(
        ["node", str(HIVE_INSTALL_DIR / "lib" / "dashboard" / "server.js"),
         "--dir", str(company_dir), "--port", str(dash_port)],
        env={**os.environ,
             "HIVE_INSTALL_DIR": str(HIVE_INSTALL_DIR),
             "HIVE_ROOT": str(HIVE_ROOT),
             "HIVE_MCP_URL": f"http://{mcp_host}:{mcp_port}/mcp"},
        stdout=dash_log_f,
        stderr=subprocess.STDOUT,
    )

    if not _wait_for_port("127.0.0.1", dash_port, timeout=10):
        print("ERROR: dashboard did not come up. Check logs.", file=sys.stderr)
        dash_proc.terminate()
        return 1

    # 4. Update processes.json with the new PID.
    try:
        data = json.loads((company_dir / "processes.json").read_text())
        found = False
        for p in data.get("processes", []):
            if p.get("role") == "company-board":
                p["pid"] = dash_proc.pid
                p["started_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                found = True
                break
        if not found:
            data.setdefault("processes", []).append({
                "pid": dash_proc.pid,
                "role": "company-board",
                "department": "infrastructure",
                "interval": "continuous",
                "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            })
        (company_dir / "processes.json").write_text(
            json.dumps(data, indent=2) + "\n")
    except Exception:
        pass  # best-effort; processes.json is re-readable on next cycle anyway

    print(f"\n  Dashboard restarted: http://localhost:{dash_port}/")
    print(f"  Agents and MCP server are untouched.")
    return 0
