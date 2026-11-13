"""MCP tool modules — SDK-free by construction (T1, 0.22).

Every module here imports ONLY stdlib + hive_mcp.shell: the
``from hive_mcp.server_compat import Server`` import that used to top
each module (the ``Server`` annotation on ``register``) was severed in
0.22 — with ``from __future__ import annotations`` in every file the
annotation is a string, so the import was runtime cost with zero
runtime use, and it dragged ``import mcp`` (~1 s) into every stopped
read. The static guard (tests/test_xdist_contract.py,
TestToolModulesSdkFree) fails the suite on a new server_compat import.

TOOL_MODULES is the single source of truth for the module list: the
SDK server (hive_mcp.server.build_server) and the SDK-free in-process
rung (hive_cli.mcp_client) both enumerate it — kept here, in the SDK-
free package, so neither side needs the other's imports.
"""

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
    "sprint",
    "reports",
    "llm_usage",
    "github",
    "release",
]
