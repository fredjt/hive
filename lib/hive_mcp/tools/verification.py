"""Verification gate tools."""

from __future__ import annotations

import json
from pathlib import Path

from hive_mcp.server_compat import Server

from hive_mcp.shell import run


def register(server: Server) -> None:
    @server.tool()
    def gates_run(
        dept: str,
        project_root: str,
        ticket_id: str | None = None,
        company_dir: str | None = None,
    ) -> dict:
        """Run deterministic verification gates against in-review tickets in `dept`.

        With `ticket_id`, gates exactly that ticket (--ticket); without it,
        the first in-review ticket in the dept is gated.

        Gates that fail bounce tickets back to in-progress with a comment.
        Gates that pass mark `gates_passed=true` on the ticket — the verification
        IC must still call `ticket_approve` before the ticket can move to done.
        """
        args = [dept, project_root, "--quiet"]
        if ticket_id:
            args += ["--ticket", ticket_id]
        return run("verification-gates", *args, company_dir=company_dir).to_dict()

    @server.tool()
    def gates_check(
        dept: str,
        ticket_id: str,
        company_dir: str | None = None,
    ) -> dict:
        """Read the most recent gate results for a single ticket."""
        from os import environ

        cd = company_dir or environ.get("COMPANY_DIR") or str(Path.cwd() / ".company")
        gates_file = Path(cd) / "departments" / dept / "components" / ticket_id / "gates.json"
        if not gates_file.is_file():
            return {"ok": False, "stderr": "no gates report found", "data": None}
        try:
            return {"ok": True, "data": json.loads(gates_file.read_text())}
        except json.JSONDecodeError as e:
            return {"ok": False, "stderr": f"invalid gates.json: {e}"}
