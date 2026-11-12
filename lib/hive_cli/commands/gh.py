"""hive gh ..."""

from __future__ import annotations

import json
import sys
from pathlib import Path

# T1 (0.20): mcp_client imports in the handler body only — present is
# pure rendering (SDK-free) and stays at module level.
from hive_cli import present


def _cd():
    p = Path.cwd() / ".company"
    return str(p) if p.is_dir() else None


def _print(obj):
    print(json.dumps(obj, indent=2))


def _status_face(res: dict, args) -> int:
    """gh status' result is a raw dict (no ok key) — a reported state,
    not an error; it exits 0 like today. --json is that dict
    byte-for-byte; the default is the aligned lines."""
    if getattr(args, "json", False):
        print(present.envelope(res))
    else:
        print(present.render_gh_status(res))
    return 0


def _pr_face(res: dict, args) -> int:
    """gh pr open/merge ride the shell envelope: --json is it
    byte-for-byte; the default is the one-line confirmation (PR
    number/URL). Exit matrix per the T1 contract: 0 iff res.ok."""
    if getattr(args, "json", False):
        print(present.envelope(res))
    elif res.get("ok"):
        print(present.render_gh_pr(res))
    else:
        err = (res.get("stderr") or "").strip()
        print(err or res.get("error") or "PR operation failed", file=sys.stderr)
    return 0 if res.get("ok") else 1


def handle(args) -> int:
    from hive_cli import mcp_client
    cd = _cd()
    base = {"company_dir": cd} if cd else {}

    if args.action == "status":
        return _status_face(mcp_client.call_tool("gh_status", base), args)

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
            return _pr_face(mcp_client.call_tool("gh_pr_open", {
                **base, "branch": args.branch, "ticket_id": args.ticket,
                "project_root": str(Path.cwd().resolve()),
            }), args)
        elif sub == "merge":
            return _pr_face(mcp_client.call_tool("gh_pr_merge", {
                **base, "branch": args.branch,
                "project_root": str(Path.cwd().resolve()),
                "method": getattr(args, "method", "merge"),
            }), args)
        else:
            print(f"unknown pr action: {sub}")
            return 1

    print(f"unknown gh action: {args.action}")
    return 1
