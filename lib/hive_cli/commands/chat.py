"""hive chat ...

Two faces (T1): the default is the human rendering (the transcript for
read, the send confirmation for send); --json prints the raw MCP
envelope byte-identical to 0.16.0 — on read the flag is no longer a
no-op (0.16 advertised it but the default output already WAS the
envelope). Exit codes unchanged (0 iff res.ok).

send takes the channel and the message (0.18 T3): author defaults to
the OS user running the command and department to the channel's own
department (dept-rd → rd; a named channel → itself) — the same
defaults the agents already carry inline, now in one place. The MCP
payload shape is unchanged; the machine contract does not move.
"""

from __future__ import annotations

import getpass
import sys

# T1 (0.20): mcp_client imports in the handler body only — present is
# pure rendering (SDK-free) and stays at module level.
from hive_cli import present
from hive_cli.commands import company_dir as _company_dir


def _channel_department(channel: str) -> str:
    """A channel's own department: dept-<name> → <name>; every other
    named channel → the channel itself (general → general)."""
    if channel.startswith("dept-") and len(channel) > len("dept-"):
        return channel[len("dept-"):]
    return channel


def handle(args) -> int:
    from hive_cli import mcp_client
    cd = _company_dir()
    if args.action == "send":
        if not args.message:
            print("error: a message is required — "
                  "usage: hive chat send <channel> <message> "
                  "[--author NAME] [--dept DEPT]",
                  file=sys.stderr)
            return 1
        author = args.author or getpass.getuser()
        department = args.dept or _channel_department(args.channel)
        payload = {"channel": args.channel, "author": author,
                   "department": department, "message": args.message}
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
