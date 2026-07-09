"""Hive MCP server — exposes ~50 tools that wrap the bash tool scripts.

Two transports are supported:
  - stdio:  used when a Claude Code agent is spawned with --mcp-config pointing
            at this file as a stdio MCP server.
  - http:   used by the dashboard (Node.js) and the CLI to invoke tools over
            localhost. Started by `hive start` or `hive dashboard`.

Tools are registered in lib/mcp/tools/<group>.py modules. Each module exposes
a `register(server)` function that adds tools via @server.tool().
"""

from __future__ import annotations

import argparse
import importlib
import os
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP


HIVE_ROOT = Path(os.environ.get("HIVE_ROOT", Path(__file__).resolve().parents[2]))


# Order matters only for stable docs/help output.
TOOL_MODULES = [
    "init",
    "tickets",
    "chat",
    "status",
    "verification",
    "worktrees",
    "agents",
    "company",
    "inbox",
    "sprint",
    "rounds",
    "reports",
    "llm_tiny",
    "github",
    "release",
]


def build_server() -> FastMCP:
    """Construct the FastMCP app with all tool modules registered."""
    server = FastMCP(
        "hive",
        instructions=(
            "Hive orchestrates a virtual company of Claude agents. "
            "Tools manage tickets, chat, sprint state, verification gates, "
            "and agent spawning. All persistent state lives in .company/ in "
            "the current project root (passed via the company_dir argument "
            "or detected from the COMPANY_DIR environment variable)."
        ),
    )

    for mod_name in TOOL_MODULES:
        try:
            mod = importlib.import_module(f"hive_mcp.tools.{mod_name}")
        except ImportError:
            # Direct file-based import (when not installed as a package)
            sys.path.insert(0, str(HIVE_ROOT / "lib"))
            mod = importlib.import_module(f"mcp.tools.{mod_name}")
        if hasattr(mod, "register"):
            mod.register(server)

    return server


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="hive-mcp",
        description="Hive MCP server (tools for the company orchestration framework)",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default="stdio",
        help="Transport: stdio (for Claude agents) or http (for dashboard/CLI)",
    )
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind host")
    parser.add_argument("--port", type=int, default=7891, help="HTTP bind port")
    args = parser.parse_args()

    server = build_server()
    if args.transport == "stdio":
        server.run(transport="stdio")
    else:
        # FastMCP's HTTP runner picks up host/port from settings before .run()
        server.settings.host = args.host
        server.settings.port = args.port
        server.run(transport="streamable-http")


if __name__ == "__main__":
    main()
