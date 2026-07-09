"""hive agent ..."""

from __future__ import annotations

import json
from pathlib import Path

from hive_cli import mcp_client


def handle(args) -> int:
    project_root = str(Path(args.project_dir).resolve())
    if args.action == "spawn-ceo":
        res = mcp_client.call_tool("agent_spawn_ceo", {"project_root": project_root, "mode": args.mode})
    elif args.action == "run-vp":
        res = mcp_client.call_tool("agent_run_vp", {"dept": args.dept, "project_root": project_root})
    elif args.action == "verify":
        res = mcp_client.call_tool("agent_verify", {
            "dept": args.dept, "ticket_id": args.ticket_id, "project_root": project_root,
        })
    print(json.dumps(res, indent=2))
    return 0 if res.get("ok") else 1
