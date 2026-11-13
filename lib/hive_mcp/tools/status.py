"""Derived state tools (status.json, roster.json)."""

from __future__ import annotations

import json
from pathlib import Path

from hive_mcp.shell import run


def register(server: Server) -> None:
    @server.tool()
    def status_get(dept: str | None = None, company_dir: str | None = None) -> dict:
        """Read the current derived status.

        Without `dept`: returns all departments. With `dept`: returns one.
        """
        from os import environ

        cd = company_dir or environ.get("COMPANY_DIR") or str(Path.cwd() / ".company")
        depts_dir = Path(cd) / "departments"
        if not depts_dir.is_dir():
            return {"ok": False, "stderr": f"no departments dir at {depts_dir}"}

        results: dict = {}
        target_depts = [dept] if dept else [d.name for d in depts_dir.iterdir() if d.is_dir()]
        for d in target_depts:
            sf = depts_dir / d / "status.json"
            if sf.is_file():
                try:
                    results[d] = json.loads(sf.read_text())
                except json.JSONDecodeError as e:
                    results[d] = {"error": f"invalid JSON: {e}"}
        return {"ok": True, "data": results}

    @server.tool()
    def status_rebuild(dept: str | None = None, company_dir: str | None = None) -> dict:
        """Force a rebuild of derived status.json files.

        Without `dept`: rebuilds all. With `dept`: rebuilds one.
        """
        args = []
        if dept:
            args += ["--dept", dept]
        return run("status-rebuild", *args, company_dir=company_dir).to_dict()

    @server.tool()
    def roster_get(
        active_only: bool = False,
        dept: str | None = None,
        company_dir: str | None = None,
    ) -> dict:
        """Read the current derived roster (active VPs and ICs).

        Triggers a rebuild before reading to ensure freshness.
        """
        args = ["list"]
        if active_only:
            args.append("--active")
        if dept:
            args += ["--dept", dept]
        return run("roster-update", *args, company_dir=company_dir).to_dict()

    @server.tool()
    def roster_rebuild(company_dir: str | None = None) -> dict:
        """Force a rebuild of roster.json from authoritative sources."""
        return run("roster-rebuild", company_dir=company_dir).to_dict()
