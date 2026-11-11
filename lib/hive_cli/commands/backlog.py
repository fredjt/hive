"""hive backlog ...

Two faces (T1): --json prints the 0.16.0 envelope byte-for-byte
(the script's ranked ticket ids on stdout — sprint-fill rides it);
the default renders the top-N table (priority, type, title, id),
whose extra columns come from the dept's backlog summary (one local
MCP call, human path only). Exit codes unchanged (0 iff res.ok).
"""

from __future__ import annotations

from hive_cli import mcp_client, present
from hive_cli.commands import company_dir as _company_dir


def _backlog_summaries(company_dir: str | None, dept: str, res: dict) -> dict:
    """{ticket_id: summary} for the ranked ids in the prioritize
    envelope's stdout — the table's PRI/TYPE/TITLE columns."""
    ids = {l.strip() for l in (res.get("stdout") or "").strip().splitlines()
           if l.strip()}
    if not ids:
        return {}
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
    cd = _company_dir()
    payload = {"dept": args.dept, "limit": args.limit}
    if cd: payload["company_dir"] = cd
    res = mcp_client.call_tool("prioritize_backlog", payload)
    if res.get("ok"):
        summaries = _backlog_summaries(cd, args.dept, res)
        return present.finish(res, args,
                              lambda r: present.render_backlog(r, args, summaries))
    return present.finish(res, args)
