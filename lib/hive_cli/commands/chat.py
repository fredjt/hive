"""hive chat ..."""

from __future__ import annotations

import json

from hive_cli import mcp_client
from hive_cli.commands import company_dir as _company_dir


def handle(args) -> int:
    cd = _company_dir()
    if args.action == "send":
        payload = {"channel": args.channel, "author": args.author,
                   "department": args.department, "message": args.message}
        if cd: payload["company_dir"] = cd
        res = mcp_client.call_tool("chat_send", payload)
    elif args.action == "read":
        payload = {"channel": args.channel}
        if args.last: payload["last"] = args.last
        if args.since: payload["since"] = args.since
        if cd: payload["company_dir"] = cd
        res = mcp_client.call_tool("chat_read", payload)
    print(json.dumps(res, indent=2))
    return 0 if res.get("ok") else 1
