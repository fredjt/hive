"""hive start | hive stop | hive status

start/stop shell out to company-start.sh / company-stop.sh DIRECTLY (not via
MCP). This is intentional — the MCP `company_start`/`company_stop` tools were
removed because spawned agents kept invoking them mid-cycle, restart-looping
the whole company. Lifecycle is human-only.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from hive_cli import mcp_client


# Resolve hive install root once. Mirrors hive_mcp.shell.HIVE_ROOT logic.
_HIVE_ROOT = Path(os.environ.get("HIVE_ROOT") or Path(__file__).resolve().parents[3])


def _project_root(args) -> str:
    return str(Path(args.project_dir).resolve())


def handle_start(args) -> int:
    project_root = _project_root(args)
    company_dir = Path(project_root) / ".company"
    log_dir = company_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "company-start.log"

    env = {
        **os.environ,
        "HIVE_ROOT": str(_HIVE_ROOT),
        "SKILL_DIR": str(_HIVE_ROOT),
    }

    print(f"Starting company at {project_root} (log: {log_path})...")
    with open(log_path, "ab") as log_f:
        proc = subprocess.Popen(
            ["bash", str(_HIVE_ROOT / "lib" / "tools" / "company-start.sh"), project_root],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log_f,
            stderr=log_f,
            start_new_session=True,
        )
    try:
        rc = proc.wait(timeout=120)
        ok = (rc == 0)
        note = ""
    except subprocess.TimeoutExpired:
        rc = None
        ok = True
        note = " (still spawning daemons in background)"

    proc_file = company_dir / "processes.json"
    n = 0
    started = ""
    if proc_file.is_file():
        try:
            data = json.loads(proc_file.read_text())
            n = len(data.get("processes", []))
            started = data.get("started_at", "")
        except json.JSONDecodeError:
            pass

    if not ok:
        print(f"company-start.sh exited {rc}. See {log_path}", file=sys.stderr)
        return 1

    dashboard_port = int(os.environ.get("DASHBOARD_PORT", "3456"))
    mcp_host = os.environ.get("HIVE_MCP_HOST", "127.0.0.1")
    mcp_port = int(os.environ.get("HIVE_MCP_PORT", "7891"))

    print()
    print(f"  ✓ Company started{note} — {n} processes at {started}")
    print()
    print(f"    Dashboard:  http://localhost:{dashboard_port}/")
    print(f"    MCP server: http://{mcp_host}:{mcp_port}/mcp")
    print(f"    Logs:       {log_path}")
    print()
    print("  To stop:    hive stop")
    print("  To panic:   hive killall")
    print()
    return 0


def handle_stop(args) -> int:
    project_root = _project_root(args)
    env = {
        **os.environ,
        "HIVE_ROOT": str(_HIVE_ROOT),
        "SKILL_DIR": str(_HIVE_ROOT),
    }
    proc = subprocess.run(
        ["bash", str(_HIVE_ROOT / "lib" / "tools" / "company-stop.sh"), project_root],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode == 0:
        print("Company stopped.")
        if proc.stderr.strip():
            print(proc.stderr, file=sys.stderr)
        return 0
    print(f"company-stop.sh exited {proc.returncode}", file=sys.stderr)
    if proc.stdout:
        print(proc.stdout, file=sys.stderr)
    if proc.stderr:
        print(proc.stderr, file=sys.stderr)
    return 1


def handle_status(args) -> int:
    project_root = _project_root(args)
    company_dir = str(Path(project_root) / ".company")
    payload: dict = {"company_dir": company_dir}
    if args.dept:
        payload["dept"] = args.dept
    res = mcp_client.call_tool("status_get", payload)
    if args.json:
        print(json.dumps(res, indent=2))
        return 0 if res.get("ok") else 1

    if not res.get("ok"):
        print(f"ERROR: {res.get('stderr') or res.get('error')}", file=sys.stderr)
        return 1

    data = res.get("data", {})
    if not data:
        print("No departments found.")
        return 0

    # Pretty print
    for dept, st in sorted(data.items()):
        if "error" in st:
            print(f"  {dept}: {st['error']}")
            continue
        health = st.get("health", "?")
        sym = {"good": "✓", "warning": "⚠", "critical": "✗"}.get(health, "·")
        tickets = st.get("tickets", {})
        bl, sp, ip = tickets.get("backlog", 0), tickets.get("sprint_current", 0), tickets.get("in_progress", 0)
        ir, dn = tickets.get("in_review", 0), tickets.get("done", 0)
        velocity = st.get("velocity_per_window", 0)
        stuck = st.get("stuck_tickets", 0)
        print(f"  {sym} {dept:<30} health={health:<8} bl={bl} sp={sp} ip={ip} ir={ir} done={dn} stuck={stuck} velocity={velocity}")
    return 0
