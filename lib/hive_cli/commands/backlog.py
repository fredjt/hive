"""hive backlog ...

Two faces (T1): --json prints the 0.16.0 envelope byte-for-byte
(the script's ranked ticket ids on stdout — sprint-fill rides it);
the default renders the top-N table (priority, type, title, id),
whose extra columns come from the dept's backlog summary (one local
MCP call, human path only). Exit codes unchanged (0 iff res.ok).

T3 (0.19): the human face stops double-reporting — the script's
`No backlog for <dept>` note stays in the --json envelope, it is not
printed on top of the empty-backlog line — and a named department
that does not exist is the roster's error (CLI-side, from the org
chart) instead of an rc-0 empty answer.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# T1 (0.20): mcp_client imports in the handler body only — present is
# pure rendering (SDK-free) and stays at module level.
from hive_cli import present
from hive_cli.commands import company_dir as _company_dir


def _known_departments(company_dir: str | None) -> list[str]:
    """The departments the org chart knows — the source ticket-list.sh
    checks against, and the root of the roster's available list. Empty
    when there is no readable org-chart document (the check stays off
    and prioritize proceeds as before)."""
    if not company_dir:
        return []
    try:
        doc = json.loads((Path(company_dir) / "org-chart.json").read_text())
    except (OSError, json.JSONDecodeError):
        return []
    depts = doc.get("departments")
    if isinstance(depts, dict):
        return sorted(depts)
    if isinstance(depts, list):
        return sorted(d.get("name") for d in depts
                      if isinstance(d, dict) and d.get("name"))
    return []


def _backlog_summaries(company_dir: str | None, dept: str, res: dict) -> dict:
    """{ticket_id: summary} for the ranked ids in the prioritize
    envelope's stdout — the table's PRI/TYPE/TITLE columns."""
    ids = {l.strip() for l in (res.get("stdout") or "").strip().splitlines()
           if l.strip()}
    if not ids:
        return {}
    from hive_cli import mcp_client
    payload = {"dept": dept, "status": "backlog"}
    if company_dir:
        payload["company_dir"] = company_dir
    list_res = mcp_client.call_tool("ticket_list", payload)
    out = {}
    for t in (list_res.get("data") or {}).get("tickets", []):
        if t.get("id") in ids:
            out[t["id"]] = t
    return out


def handle(args) -> int:
    from hive_cli import mcp_client
    cd = _company_dir()
    known = _known_departments(cd)
    if known and args.dept not in known:
        # T3 (0.19): the roster's face verbatim — a named department
        # that does not exist is an error with the available list, not
        # an rc-0 empty answer (the script's `No backlog for X` note
        # treated a wrong department as a healthy empty backlog).
        print(f"ERROR: Department '{args.dept}' not found "
              f"(available: {', '.join(known)})", file=sys.stderr)
        return 1
    payload = {"dept": args.dept, "limit": args.limit}
    if cd: payload["company_dir"] = cd
    res = mcp_client.call_tool("prioritize_backlog", payload)
    if res.get("ok"):
        summaries = _backlog_summaries(cd, args.dept, res)
        return present.finish(res, args,
                              lambda r: present.render_backlog(r, args, summaries),
                              pass_stderr=False)
    return present.finish(res, args)
