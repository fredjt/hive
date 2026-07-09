"""HTTP/stdio client for the hive MCP server.

Uses the official mcp Python SDK to handle session lifecycle correctly
(initialize → tools/call) for both Streamable HTTP and stdio transports.
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client


# HIVE_INSTALL_DIR — where the venv + MCP server source live (global).
HIVE_INSTALL_DIR = Path(os.environ.get("HIVE_INSTALL_DIR",
                                       Path(__file__).resolve().parents[2]))
# HIVE_ROOT — per-project scaffold root (set by bin/hive when a sentinel
# was found). Tools find their lib/tools/ here. Falls back to install dir.
HIVE_ROOT = Path(os.environ.get("HIVE_ROOT", HIVE_INSTALL_DIR))
MCP_HOST = os.environ.get("HIVE_MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.environ.get("HIVE_MCP_PORT", "7891"))


def _http_endpoint() -> str:
    return f"http://{MCP_HOST}:{MCP_PORT}/mcp"


def _is_http_server_up() -> bool:
    """Quick TCP probe — is the MCP HTTP server reachable?"""
    try:
        # Even a 400 response means a server is listening
        r = httpx.post(_http_endpoint(), json={}, timeout=1.0,
                       headers={"Accept": "application/json, text/event-stream"})
        return r.status_code < 500
    except httpx.RequestError:
        return False


@asynccontextmanager
async def _http_session():
    """Open an MCP ClientSession over Streamable HTTP."""
    async with streamablehttp_client(_http_endpoint()) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            yield session


@asynccontextmanager
async def _stdio_session():
    """Open an MCP ClientSession over stdio (one-shot subprocess)."""
    venv_py = HIVE_INSTALL_DIR / "venv" / "bin" / "python"
    server_script = HIVE_INSTALL_DIR / "lib" / "hive_mcp" / "server.py"
    params = StdioServerParameters(
        command=str(venv_py),
        args=[str(server_script), "--transport", "stdio"],
        env={**os.environ,
             "HIVE_INSTALL_DIR": str(HIVE_INSTALL_DIR),
             "HIVE_ROOT": str(HIVE_ROOT)},
    )
    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            yield session


async def _call_async(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Pick a transport (HTTP if up, else stdio) and call the tool."""
    session_cm = _http_session() if _is_http_server_up() else _stdio_session()
    async with session_cm as session:
        result = await session.call_tool(name, arguments=arguments)

    # The tool's return value is in result.content (a list of TextContent etc.)
    out = result.structuredContent if hasattr(result, "structuredContent") and result.structuredContent else None
    if out:
        # FastMCP wraps single-dict returns under a "result" key in structuredContent
        if isinstance(out, dict) and set(out.keys()) == {"result"}:
            return out["result"]
        return out
    if result.content:
        first = result.content[0]
        text = getattr(first, "text", None)
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"ok": True, "stdout": text}
    return {"ok": not result.isError, "data": None}


def call_tool(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """Sync wrapper around the async MCP call."""
    arguments = arguments or {}
    try:
        return asyncio.run(_call_async(name, arguments))
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


async def _list_async() -> list[str]:
    session_cm = _http_session() if _is_http_server_up() else _stdio_session()
    async with session_cm as session:
        resp = await session.list_tools()
        return sorted(t.name for t in resp.tools)


def list_tools() -> list[str]:
    try:
        return asyncio.run(_list_async())
    except Exception:
        # Fallback: introspect the server module directly without spawning
        import sys
        sys.path.insert(0, str(HIVE_ROOT / "lib"))
        from hive_mcp.server import build_server  # noqa: E402
        server = build_server()
        return sorted(server._tool_manager._tools.keys())
