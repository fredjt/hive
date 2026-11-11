"""hive chat ...

Two faces (T1): the default is the human rendering (the transcript for
read, the send confirmation for send); --json prints the raw MCP
envelope byte-identical to 0.16.0 — on read the flag is no longer a
no-op (0.16 advertised it but the default output already WAS the
envelope). Exit codes unchanged (0 iff res.ok).
"""

from __future__ import annotations

import sys

from hive_cli import mcp_client, present
from hive_cli.commands import company_dir as _company_dir


def handle(args) -> int:
    cd = _company_dir()
    if args.action == "send":
        payload = {"channel": args.channel, "author": args.author,
                   "department": args.department, "message": args.message}
        if cd: payload["company_dir"] = cd
        res = mcp_client.call_tool("chat_send", payload)
        return present.finish(res, args,
                              lambda r: present.render_chat_send(args.channel))
    elif args.action == "read":
        payload = {"channel": args.channel}
        if args.last: payload["last"] = args.last
        if args.since: payload["since"] = args.since
        if cd: payload["company_dir"] = cd
        res = mcp_client.call_tool("chat_read", payload)
        return present.finish(res, args,
                              lambda r: present.render_chat_read(r.get("data") or {}))
    elif args.action == "channels":
        payload = {}
        if cd: payload["company_dir"] = cd
        res = mcp_client.call_tool("chat_channels", payload)
        return present.finish(res, args,
                              lambda r: present.render_chat_channels(
                                  r.get("data") or {}))
    print(f"unknown chat action: {args.action}", file=sys.stderr)
    return 1
