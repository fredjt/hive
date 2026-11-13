"""Company process control — read-only via MCP.

Lifecycle control (start/stop) is intentionally NOT exposed via MCP. Spawned
agents (VPs, ICs, CEO) were calling `company_start` mid-cycle, which invoked
company-stop.sh first and killed every other in-flight turn — including the
caller. Lifecycle is human-only: invoke via `hive start` / `hive stop` CLI or
the dashboard's Start/Stop buttons (both shell out to the bash scripts
directly, bypassing MCP).

`company_status` remains exposed because reading processes.json is harmless
and useful for agents to know whether the company is up.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from hive_mcp.shell import HIVE_ROOT  # noqa: F401  (kept for re-import compat)


def register(server: Server) -> None:
    @server.tool()
    def company_status(project_root: str) -> dict:
        """Read processes.json for daemon status."""
        proc_file = Path(project_root) / ".company" / "processes.json"
        if not proc_file.is_file():
            return {"ok": False, "stderr": "no processes.json (company not started?)", "data": None}
        try:
            data = json.loads(proc_file.read_text())
        except json.JSONDecodeError as e:
            return {"ok": False, "stderr": f"invalid processes.json: {e}"}

        # Annotate each process with current alive/dead status
        for p in data.get("processes", []):
            pid = p.get("pid")
            if pid:
                try:
                    os.kill(pid, 0)
                    p["alive"] = True
                except (ProcessLookupError, PermissionError):
                    p["alive"] = False
        return {"ok": True, "data": data}
