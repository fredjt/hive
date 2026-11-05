"""hive ticket ..."""

from __future__ import annotations

import json
import sys

from hive_cli import mcp_client
from hive_cli.commands import company_dir as _company_dir


def handle(args) -> int:
    cd = _company_dir()
    action = args.action
    if action == "create":
        payload = {
            "dept": args.dept, "type": args.type, "priority": args.priority,
            "title": args.title, "description": args.description,
            "created_by": args.created_by, "source": args.source,
        }
        if args.estimate:
            payload["estimate"] = args.estimate
        if cd: payload["company_dir"] = cd
        res = mcp_client.call_tool("ticket_create", payload)
    elif action == "get":
        payload = {"dept": args.dept, "ticket_id": args.ticket_id}
        if cd: payload["company_dir"] = cd
        res = mcp_client.call_tool("ticket_get", payload)
    elif action == "list":
        payload = {}
        if args.dept: payload["dept"] = args.dept
        if args.status: payload["status"] = args.status
        if getattr(args, "priority", None):
            payload["priority"] = args.priority
        if cd: payload["company_dir"] = cd
        if not args.dept:
            print("ERROR: 'hive ticket list' requires a department argument", file=sys.stderr)
            return 1
        res = mcp_client.call_tool("ticket_list", payload)
    elif action == "move":
        payload = {
            "dept": args.dept, "ticket_id": args.ticket_id,
            "new_status": args.new_status, "comment": args.comment,
            "author": args.author, "force": args.force,
        }
        if args.reason: payload["reason"] = args.reason
        if cd: payload["company_dir"] = cd
        res = mcp_client.call_tool("ticket_move", payload)
    elif action == "claim":
        payload = {
            "dept": args.dept, "ticket_id": args.ticket_id,
            "role": args.role, "agent_id": args.agent_id, "agent_name": args.agent_name,
        }
        if args.worktree_path: payload["worktree_path"] = args.worktree_path
        if cd: payload["company_dir"] = cd
        res = mcp_client.call_tool("ticket_claim", payload)
    elif action == "comment":
        payload = {"dept": args.dept, "ticket_id": args.ticket_id,
                   "author": args.author, "comment": args.comment}
        if cd: payload["company_dir"] = cd
        res = mcp_client.call_tool("ticket_comment", payload)
    elif action == "approve":
        payload = {"dept": args.dept, "ticket_id": args.ticket_id,
                   "verifier": args.verifier, "rationale": args.rationale}
        if cd: payload["company_dir"] = cd
        res = mcp_client.call_tool("ticket_approve", payload)
    else:
        print(f"unknown ticket action: {action}", file=sys.stderr)
        return 1

    print(json.dumps(res, indent=2))
    return 0 if res.get("ok") else 1
