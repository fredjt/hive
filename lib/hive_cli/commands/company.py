"""hive start | hive stop | hive status

start/stop shell out to company-start.sh / company-stop.sh DIRECTLY (not via
MCP). This is intentional — the MCP `company_start`/`company_stop` tools were
removed because spawned agents kept invoking them mid-cycle, restart-looping
the whole company. Lifecycle is human-only.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
import subprocess
import sys
from pathlib import Path

from hive_cli import mcp_client


# Resolve hive install root once (G9: lib/hive_paths.py is the single
# implementation — the mirrored copies are gone).
from hive_paths import install_root

_HIVE_ROOT = install_root()


def _project_root(args) -> str:
    return str(Path(args.project_dir).resolve())


def _pid_alive(pid) -> bool:
    """Liveness check: signal 0, with a refinement — kill(0) also succeeds
    for zombies (terminated but unreaped), which are NOT running."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        # comm may contain spaces — the state field follows the last ')'
        # in the "pid (comm) state ..." layout.
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except OSError:
        return True  # no /proc (non-Linux) — trust the signal-0 answer
    return state != "Z"


def _daemon_table(company_dir: str) -> list[dict]:
    """Per-daemon liveness + supervisor bookkeeping from processes.json.

    This is the machine-readable half of the restart policy in
    company-health.sh: restart_count is incremented on every supervised
    restart, and dead/dead_reason are written when a daemon exhausts
    HIVE_MAX_RESTARTS (never restarted again within the session).
    """
    proc_file = Path(company_dir) / "processes.json"
    if not proc_file.is_file():
        return []
    try:
        data = json.loads(proc_file.read_text())
    except json.JSONDecodeError:
        return []
    out = []
    for p in data.get("processes", []):
        pid = p.get("pid")
        out.append({
            "role": p.get("role", "unknown"),
            "department": p.get("department", ""),
            "pid": pid,
            "alive": _pid_alive(pid),
            "restart_count": p.get("restart_count", 0),
            "dead": bool(p.get("dead")),
            "dead_reason": p.get("dead_reason", ""),
            "started_at": p.get("started_at", ""),
        })
    return out


def _print_sprint(sprint: dict | None) -> None:
    """Human-readable sprint line for `hive status` (A1: idle must read as
    a state, not as a hang; A2: a halt must read as an action needed)."""
    if not sprint:
        return
    if sprint.get("halted"):
        h = sprint["halted"]
        limit = h.get("limit")
        limit_note = f"; outage threshold: {limit}" if limit else ""
        print()
        print(f"  \033[31m\u26d4 Sprint {sprint.get('sprint_id', '?')} HALTED: {h.get('reason', '')}\033[0m"
              f"  ({h.get('ts', '')}{limit_note}; fix the LLM endpoint, then 'hive start')")
        return
    stage = sprint.get("stage", "")
    if stage == "idle":
        label = "idle — no work (runner polls the backlog; file a ticket to wake it)"
    else:
        label = f"stage={stage}"
    print()
    print(f"  Sprint: {sprint.get('sprint_id', '?')} — {label}")


def _print_daemons(daemons: list[dict]) -> None:
    """Human-readable daemon section for `hive status`."""
    if not daemons:
        return
    print()
    print("Daemons (supervisor-managed by company-health.sh):")
    for d in daemons:
        if d["alive"]:
            sym = "✓"
        elif d["dead"]:
            sym = "✗"
        else:
            sym = "·"
        line = f"  {sym} {d['role']:<24} pid={d['pid']}  restarts={d['restart_count']}"
        if d["dead"]:
            line += f"  DEAD — {d['dead_reason']} (fix: hive stop && hive start)"
        elif not d["alive"]:
            line += "  (not running — supervisor restarts dead daemons up to budget)"
        print(line)


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


def _sprint_info(company_dir: str) -> dict | None:
    """A1/A2: sprint state straight from sprints/current.json — independent
    of the MCP server. 'idle' (a zero-ticket sprint waiting for work) and
    the halt marker are the two states that must be visible here: an idle
    company is healthy-but-waiting, a halted company needs operator action."""
    cur = Path(company_dir) / "sprints" / "current.json"
    out: dict = {}
    if cur.is_file():
        try:
            data = json.loads(cur.read_text())
            out["sprint_id"] = data.get("sprint_id", "")
            out["stage"] = data.get("stage", "")
            out["stage_started_at"] = data.get("stage_started_at", "")
        except json.JSONDecodeError:
            pass
    halt = Path(company_dir) / "rounds" / "halt"
    if halt.is_file():
        try:
            h = json.loads(halt.read_text())
            out["halted"] = {
                "reason": h.get("reason", ""),
                "ts": h.get("ts", ""),
                "sprint_id": h.get("sprint_id", ""),
                "endpoint": h.get("endpoint", ""),
                "limit": h.get("limit"),
                "failed_spawns": h.get("failed_spawns"),
            }
        except json.JSONDecodeError:
            out["halted"] = {"reason": "halt marker unreadable", "ts": ""}
    return out or None


def _config_drift_note(company_dir: str) -> str | None:
    """D2: the user config is read once at daemon spawn — if the file was
    modified after the recorded daemon start, the running daemons still
    hold the OLD config. Return a one-line notice when that is the case
    (None when there is no drift, or nothing to compare against)."""
    cfg = Path(os.environ.get("HIVE_CONFIG")
               or (Path.home() / ".config" / "hive" / "config.toml"))
    if not cfg.is_file():
        return None
    proc_file = Path(company_dir) / "processes.json"
    if not proc_file.is_file():
        return None
    try:
        data = json.loads(proc_file.read_text())
    except json.JSONDecodeError:
        return None
    starts = []
    for p in data.get("processes", []):
        ts = p.get("started_at") or ""
        if ts:
            try:
                starts.append(
                    datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp())
            except ValueError:
                pass
    if not starts:
        return None
    if cfg.stat().st_mtime > max(starts):
        return ("config.toml changed after daemons started — "
                "run 'hive stop && hive start' to apply the new config "
                "(daemons read the config once at spawn)")
    return None


def handle_status(args) -> int:
    project_root = _project_root(args)
    company_dir = str(Path(project_root) / ".company")
    payload: dict = {"company_dir": company_dir}
    if args.dept:
        payload["dept"] = args.dept
    res = mcp_client.call_tool("status_get", payload)
    # Daemon state comes straight from processes.json — independent of the
    # MCP server, which matters when the MCP daemon is exactly what died.
    daemons = _daemon_table(company_dir)
    # Sprint state comes straight from sprints/current.json — same reason
    # (A1: the idle stage must be visible even when the MCP server is down).
    sprint = _sprint_info(company_dir)
    # D2: config drift — the note must surface even when the MCP server
    # is down (that is precisely when the operator is looking for why
    # nothing picks up the new config).
    drift = _config_drift_note(company_dir)
    if args.json:
        if isinstance(res, dict):
            res["daemons"] = daemons
            if sprint:
                res["sprint"] = sprint
            if drift:
                res["config_drift"] = drift
        print(json.dumps(res, indent=2))
        return 0 if res.get("ok") else 1

    if drift:
        print(f"\033[33m\u26a0 {drift}\033[0m")
    if not res.get("ok"):
        print(f"ERROR: {res.get('stderr') or res.get('error')}", file=sys.stderr)
        _print_sprint(sprint)
        _print_daemons(daemons)
        return 1

    data = res.get("data", {})
    if not data:
        print("No departments found.")
    else:
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
    _print_sprint(sprint)
    _print_daemons(daemons)
    if drift:
        print(f"\033[33m\u26a0 {drift}\033[0m")
    return 0
