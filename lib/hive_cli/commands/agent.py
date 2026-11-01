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
    else:
        # Unknown action — the parser only offers spawn-ceo / run-vp
        # (the old `verify` action called the removed agent_verify MCP
        # tool and was deleted).
        return 2
    print(json.dumps(res, indent=2))
    return 0 if res.get("ok") else 1
