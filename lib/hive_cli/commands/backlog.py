"""hive backlog ..."""

from __future__ import annotations

import json
from pathlib import Path

from hive_cli import mcp_client


def handle(args) -> int:
    cd = Path.cwd() / ".company"
    payload = {"dept": args.dept, "limit": args.limit}
    if cd.is_dir(): payload["company_dir"] = str(cd)
    res = mcp_client.call_tool("prioritize_backlog", payload)
    print(json.dumps(res, indent=2))
    return 0 if res.get("ok") else 1
