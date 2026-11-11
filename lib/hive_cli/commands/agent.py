"""hive agent ...

Two faces (T1): --json prints the raw MCP result byte-for-byte
(agent-spawn's stdout is the stream-json event stream — the
correct machine face); the default is the one-line confirmation
with the agent id and the log file to tail. Exit codes unchanged
(0 iff res.ok).
"""

from __future__ import annotations

from pathlib import Path

from hive_cli import mcp_client, present
from hive_cli.commands import company_dir as _company_dir


def _agent_error(res: dict) -> str:
    """The human error line for a failed spawn: the harness reports
    its real error on the envelope's STDOUT (its own stderr is the
    spawn plumbing — venv warnings), so stdout leads here."""
    return ((res.get("stdout") or "").strip()
            or (res.get("stderr") or "").strip()
            or res.get("error") or "agent spawn failed")


def handle(args) -> int:
    project_root = str(Path(args.project_dir).resolve())
    cd = _company_dir()
    if args.action == "spawn-ceo":
        res = mcp_client.call_tool(
            "agent_spawn_ceo", {"project_root": project_root, "mode": args.mode})
        return present.finish(
            res, args,
            lambda r: present.render_agent_spawn("ceo", args.mode, cd),
            error=_agent_error)
    elif args.action == "run-vp":
        res = mcp_client.call_tool(
            "agent_run_vp", {"dept": args.dept, "project_root": project_root})
        return present.finish(
            res, args,
            lambda r: present.render_agent_run_vp(args.dept, cd),
            error=_agent_error)
    else:
        # Unknown action — the parser only offers spawn-ceo / run-vp
        # (the old `verify` action called the removed agent_verify MCP
        # tool and was deleted).
        return 2
