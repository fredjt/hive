"""hive ticket ...

Two faces (T1): the default is the human rendering from
lib/hive_cli/present.py; --json prints the raw MCP envelope
byte-identical to 0.16.0. The exit-code matrix is unchanged
(0 iff res.ok); a non-empty envelope stderr is always shown.
"""

from __future__ import annotations

import getpass
import sys

# T1 (0.20): mcp_client imports in the handler body only — present is
# pure rendering (SDK-free) and stays at module level.
from hive_cli import present
from hive_cli.commands import company_dir as _company_dir


def handle(args) -> int:
    from hive_cli import mcp_client
    cd = _company_dir()
    action = args.action
    if action == "create":
        # T2 (0.20): the human's hand — title + named flags; description
        # is optional (the script requires dept/type/title only) — an
        # omitted one passes the tool's required `description` parameter
        # as the empty string the script already tolerates.
        payload = {
            "dept": args.dept, "type": args.type, "priority": args.priority,
            "title": args.title, "description": args.description or "",
            "created_by": args.created_by, "source": args.source,
        }
        if args.estimate:
            payload["estimate"] = args.estimate
        if cd: payload["company_dir"] = cd
        res = mcp_client.call_tool("ticket_create", payload)
        return present.finish(res, args,
                              lambda r: present.render_ticket_create(r, args))
    elif action == "get":
        payload = {"dept": args.dept, "ticket_id": args.ticket_id}
        if cd: payload["company_dir"] = cd
        res = mcp_client.call_tool("ticket_get", payload)
        return present.finish(res, args,
                              lambda r: present.render_ticket_card(r.get("data") or {}))
    elif action == "list":
        payload = {}
        if args.dept: payload["dept"] = args.dept
        if args.status: payload["status"] = args.status
        if getattr(args, "priority", None):
            payload["priority"] = args.priority
        if cd: payload["company_dir"] = cd
        res = mcp_client.call_tool("ticket_list", payload)
        return present.finish(res, args,
                              lambda r: present._render_list(r.get("data") or {}))
    elif action == "move":
        # T2 (0.20): the flags are the human's face — comment/author/reason
        # left the positional line; author defaults to the running OS user
        # (the chat-send precedent, 0.18 T3). The MCP payload shape is
        # unchanged (the same keys, the same defaults the agents carry).
        payload = {
            "dept": args.dept, "ticket_id": args.ticket_id,
            "new_status": args.new_status, "comment": args.comment or "",
            "author": args.author or getpass.getuser(), "force": args.force,
        }
        if args.reason: payload["reason"] = args.reason
        if cd: payload["company_dir"] = cd
        res = mcp_client.call_tool("ticket_move", payload)
        return present.finish(res, args, present.render_action)
    elif action == "claim":
        payload = {
            "dept": args.dept, "ticket_id": args.ticket_id,
            "role": args.role, "agent_id": args.agent_id, "agent_name": args.agent_name,
        }
        if args.worktree_path: payload["worktree_path"] = args.worktree_path
        if cd: payload["company_dir"] = cd
        res = mcp_client.call_tool("ticket_claim", payload)
        return present.finish(res, args, present.render_action)
    elif action == "comment":
        # T2 (0.20): the body is the third positional; author defaults to
        # the running OS user (the chat-send precedent, 0.18 T3).
        payload = {"dept": args.dept, "ticket_id": args.ticket_id,
                   "author": args.author or getpass.getuser(),
                   "comment": args.comment}
        if cd: payload["company_dir"] = cd
        res = mcp_client.call_tool("ticket_comment", payload)
        return present.finish(res, args, present.render_action)
    elif action == "approve":
        payload = {"dept": args.dept, "ticket_id": args.ticket_id,
                   "verifier": args.verifier, "rationale": args.rationale}
        if cd: payload["company_dir"] = cd
        res = mcp_client.call_tool("ticket_approve", payload)
        return present.finish(res, args, present.render_action)
    else:
        print(f"unknown ticket action: {action}", file=sys.stderr)
        return 1
