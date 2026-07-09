"""hive gh ..."""

from __future__ import annotations

import json
from pathlib import Path

from hive_cli import mcp_client


def _cd():
    p = Path.cwd() / ".company"
    return str(p) if p.is_dir() else None


def _print(obj):
    print(json.dumps(obj, indent=2))


def handle(args) -> int:
    cd = _cd()
    base = {"company_dir": cd} if cd else {}

    if args.action == "status":
        _print(mcp_client.call_tool("gh_status", base))
        return 0

    if args.action == "issues":
        _print(mcp_client.call_tool("gh_issues", base))
        return 0

    if args.action == "mirror":
        _print(mcp_client.call_tool("gh_issue_mirror_now",
            {**base, "project_root": str(Path.cwd().resolve())}))
        return 0

    if args.action == "pr":
        sub = args.pr_action
        if sub == "open":
            _print(mcp_client.call_tool("gh_pr_open", {
                **base, "branch": args.branch, "ticket_id": args.ticket,
                "project_root": str(Path.cwd().resolve()),
            }))
        elif sub == "merge":
            _print(mcp_client.call_tool("gh_pr_merge", {
                **base, "branch": args.branch,
                "project_root": str(Path.cwd().resolve()),
                "method": getattr(args, "method", "merge"),
            }))
        elif sub == "reject":
            _print(mcp_client.call_tool("gh_pr_reject", {
                **base, "branch": args.branch, "ticket_id": args.ticket,
                "project_root": str(Path.cwd().resolve()),
                "reason": args.reason,
            }))
        else:
            print(f"unknown pr action: {sub}")
            return 1
        return 0

    print(f"unknown gh action: {args.action}")
    return 1
