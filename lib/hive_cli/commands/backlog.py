"""hive backlog ..."""

from __future__ import annotations

import json

from hive_cli import mcp_client
from hive_cli.commands import company_dir as _company_dir


def handle(args) -> int:
    cd = _company_dir()
    payload = {"dept": args.dept, "limit": args.limit}
    if cd: payload["company_dir"] = cd
    res = mcp_client.call_tool("prioritize_backlog", payload)
    print(json.dumps(res, indent=2))
    return 0 if res.get("ok") else 1
