"""hive inbox ..."""

from __future__ import annotations

import json
from pathlib import Path

from hive_cli import mcp_client


def _cd():
    p = Path.cwd() / ".company"
    return str(p) if p.is_dir() else None


def handle(args) -> int:
    cd = _cd()
    if args.action == "file":
        payload = {"type": args.type, "dept": args.dept, "content": args.content}
        if cd: payload["company_dir"] = cd
        res = mcp_client.call_tool("inbox_write", payload)
    elif args.action == "process":
        payload = {"dry_run": args.dry_run}
        if cd: payload["company_dir"] = cd
        res = mcp_client.call_tool("inbox_process", payload)
    print(json.dumps(res, indent=2))
    return 0 if res.get("ok") else 1
