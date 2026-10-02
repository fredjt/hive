"""hive dashboard — start MCP HTTP server + Node dashboard, open browser."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

import httpx


# HIVE_INSTALL_DIR — where venv, MCP server source, and dashboard server.js
# live (the global install). Set by the bin/hive launcher; falls back to
# walking up from this file's location for direct-from-source dev runs.
HIVE_INSTALL_DIR = Path(os.environ.get("HIVE_INSTALL_DIR",
                                       Path(__file__).resolve().parents[3]))

# HIVE_ROOT — global install (projects use installed scripts directly).
# Set by the launcher; falls back to the install dir for dev runs.
HIVE_ROOT = Path(os.environ.get("HIVE_ROOT", HIVE_INSTALL_DIR))


def _wait_for_port(host: str, port: int, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            httpx.get(f"http://{host}:{port}/", timeout=0.5)
            return True
        except httpx.RequestError:
            time.sleep(0.2)
    return False


def handle(args) -> int:
    if shutil.which("node") is None:
        print("ERROR: node is required for the dashboard but not on PATH.", file=sys.stderr)
        return 1

    project_dir = Path(args.project_dir).resolve()
    company_dir = project_dir / ".company"
    if not company_dir.is_dir():
        print(f"NOTE: no .company/ at {project_dir}. Dashboard will offer 'create new project' on first page.")

    venv_py = HIVE_INSTALL_DIR / "venv" / "bin" / "python"
    if not venv_py.is_file():
        print(f"ERROR: venv not found at {venv_py}. Run packaging/install.sh first.", file=sys.stderr)
        return 1

    mcp_host = os.environ.get("HIVE_MCP_HOST", "127.0.0.1")
    mcp_port = int(os.environ.get("HIVE_MCP_PORT", "7891"))
    dash_port = args.port

    # 1. Start MCP server (HTTP transport)
    mcp_log = company_dir / "logs" / "mcp-server.log"
    if company_dir.is_dir():
        mcp_log.parent.mkdir(parents=True, exist_ok=True)
        mcp_log_f = open(mcp_log, "ab")
    else:
        mcp_log_f = subprocess.DEVNULL

    print(f"Starting MCP server on http://{mcp_host}:{mcp_port}/mcp ...")
    mcp_proc = subprocess.Popen(
        [str(venv_py), str(HIVE_INSTALL_DIR / "lib" / "hive_mcp" / "server.py"),
         "--transport", "http", "--host", mcp_host, "--port", str(mcp_port)],
        env={**os.environ,
             "HIVE_INSTALL_DIR": str(HIVE_INSTALL_DIR),
             "HIVE_ROOT": str(HIVE_ROOT)},
        stdout=mcp_log_f,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    if not _wait_for_port(mcp_host, mcp_port, timeout=10):
        print("ERROR: MCP server did not come up. Check logs.", file=sys.stderr)
        mcp_proc.terminate()
        return 1

    # 2. Start Node dashboard
    dash_log = company_dir / "logs" / "dashboard.log"
    if company_dir.is_dir():
        dash_log_f = open(dash_log, "ab")
    else:
        dash_log_f = subprocess.DEVNULL

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
        mcp_proc.terminate()
        return 1

    # 3. Open browser
    if not args.no_open:
        webbrowser.open(f"http://localhost:{dash_port}/")

    print(f"\nHive dashboard: http://localhost:{dash_port}/")
    print(f"MCP server:     http://{mcp_host}:{mcp_port}/mcp")
    print("Ctrl-C to stop both.")

    # Keep running until Ctrl-C
    def _shutdown(signum, frame):
        print("\nShutting down...")
        for p in (dash_proc, mcp_proc):
            try:
                p.terminate()
                p.wait(timeout=5)
            except Exception:
                p.kill()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)
    try:
        dash_proc.wait()
    except KeyboardInterrupt:
        _shutdown(None, None)
    return 0
