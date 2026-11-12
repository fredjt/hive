"""hive tool / hive tools — direct MCP tool invocation + listing."""

from __future__ import annotations

import json
import sys


def handle_invoke(args) -> int:
    """hive tool <name> --arg key=value ..."""
    # T1 (0.20): the mcp_client import is in the handler body — this
    # is a tool command, so it pays the transport (and its lazy SDK
    # import); no other command does.
    from hive_cli import mcp_client
    payload = {}
    for kv in args.arg:
        if "=" not in kv:
            print(f"ERROR: --arg expects key=value, got: {kv}", file=sys.stderr)
            return 1
        k, v = kv.split("=", 1)
        # Try JSON-parse value (for numbers, bools, lists), else use as string
        try:
            payload[k] = json.loads(v)
        except json.JSONDecodeError:
            payload[k] = v

    res = mcp_client.call_tool(args.name, payload)
    print(json.dumps(res, indent=2))
    return 0 if res.get("ok") else 1


def handle_list(args) -> int:
    """hive tools — list MCP tools."""
    from hive_cli import mcp_client
    try:
        tools = mcp_client.list_tools()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(tools, indent=2))
    else:
        for t in sorted(tools):
            print(t)
    return 0
