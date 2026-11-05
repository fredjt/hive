"""hive round ..."""

from __future__ import annotations

import json

from hive_cli import mcp_client
from hive_cli.commands import company_dir as _company_dir


def _print(obj):
    print(json.dumps(obj, indent=2))


def handle(args) -> int:
    cd = _company_dir()
    base = {"company_dir": cd} if cd else {}

    if args.action == "status":
        _print(mcp_client.call_tool("round_get_current", base))
        return 0

    if args.action == "next":
        _print(mcp_client.call_tool("round_get_next", base))
        return 0

    if args.action == "history":
        _print(mcp_client.call_tool("round_history", {**base, "limit": args.limit}))
        return 0

    if args.action == "get":
        _print(mcp_client.call_tool("round_get", {**base, "round_id": args.round_id}))
        return 0

    if args.action == "enqueue":
        payload = {
            **base,
            "agent": args.agent, "dept": args.dept, "role": args.role,
            "kind": args.kind, "target": args.target,
            "queued_by": "manual-cli",
        }
        if args.ticket:        payload["ticket"]        = args.ticket
        if args.prompt_extra:  payload["prompt_extra"]  = args.prompt_extra
        res = mcp_client.call_tool("round_enqueue", payload)
        _print(res)
        return 0 if res.get("ok", False) else 1

    if args.action == "advance":
        _print(mcp_client.call_tool("round_advance", base))
        return 0

    if args.action == "promote":
        _print(mcp_client.call_tool("round_promote_next", base))
        return 0

    print(f"unknown round action: {args.action}")
    return 1
