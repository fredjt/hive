"""hive gh ..."""

from __future__ import annotations

import json
import sys
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

    if args.action == "push":
        # Item 5: the per-company push/PR opt-in marker.
        cd = Path.cwd() / ".company"
        if not cd.is_dir():
            print("no .company here - run 'hive init' first", file=sys.stderr)
            return 1
        marker = cd / "push-enabled"
        sub = args.push_action
        if sub == "enable":
            marker.write_text("push enabled for this company (hive gh push enable). Pushes, PRs and GitHub releases go to the company origin remote.\n")
            print(f"push enabled: {marker}")
            print("branches/PRs/releases will now be pushed to the company's origin remote")
        elif sub == "disable":
            try:
                marker.unlink()
            except FileNotFoundError:
                pass
            print("push disabled: pushes and PRs are soft no-ops for this company")
        else:
            print(f"unknown push action: {sub}")
            return 1
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
        else:
            print(f"unknown pr action: {sub}")
            return 1
        return 0

    print(f"unknown gh action: {args.action}")
    return 1
