"""HTTP/stdio client for the hive MCP server.

Transport preference (C.2: one protocol implementation, two consumers):
  1. The official mcp Python SDK — the fast path, used whenever it is
     installed (the CLI runs in the install venv, where it is).
  2. The shared SDK-free Streamable HTTP client (lib/core/mcp_http.py,
     httpx only) — used when the mcp SDK is not importable, so tool calls
     still work in environments that ship the CLI without the SDK.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.client.streamable_http import streamable_http_client
    _HAS_MCP_SDK = True
except ImportError:  # SDK-free environment (C.2)
    ClientSession = StdioServerParameters = None  # type: ignore
    stdio_client = streamable_http_client = None  # type: ignore
    _HAS_MCP_SDK = False


# HIVE_INSTALL_DIR — where the venv + MCP server source live (global).
HIVE_INSTALL_DIR = Path(os.environ.get("HIVE_INSTALL_DIR",
                                       Path(__file__).resolve().parents[2]))
# HIVE_ROOT — global install root. Tools find their lib/tools/ here.
HIVE_ROOT = Path(os.environ.get("HIVE_ROOT", HIVE_INSTALL_DIR))
MCP_HOST = os.environ.get("HIVE_MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.environ.get("HIVE_MCP_PORT", "7891"))


def _http_endpoint() -> str:
    return f"http://{MCP_HOST}:{MCP_PORT}/mcp"


def _load_sdk_free_client():
    """The shared SDK-free Streamable HTTP client (lib/core/mcp_http.py) —
    a normal package import (G6 retired the path-based load)."""
    import sys
    lib_dir = str(Path(__file__).resolve().parent.parent)
    if lib_dir not in sys.path:
        sys.path.insert(0, lib_dir)
    from core import mcp_http
    return mcp_http


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
    # mcp 1.x yields (read_stream, write_stream, session_manager);
    # mcp 2.x yields just (read_stream, write_stream). Index into the tuple
    # so both SDK generations work — unpacking 3 names on 2.x raises
    # ValueError inside the TaskGroup and kills every HTTP tool call.
    async with streamable_http_client(_http_endpoint()) as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            yield session


def _stdio_python() -> str:
    """Interpreter for the stdio MCP server child.

    Prefer the install venv (the installed layout). When the install
    dir is a source checkout with no venv (the documented PYTHONPATH=lib
    dev mode), use this interpreter — it just imported the mcp SDK
    successfully, so it has the server's dependencies. Same contract as
    agent-spawn.sh's venv fallback: degrade with a note, don't die with
    a FileNotFoundError.
    """
    venv_py = HIVE_INSTALL_DIR / "venv" / "bin" / "python"
    if venv_py.is_file():
        return str(venv_py)
    print(f"NOTE: install venv not found at {venv_py}; spawning the stdio "
          f"MCP server with {sys.executable} instead.", file=sys.stderr)
    return sys.executable


@asynccontextmanager
async def _stdio_session():
    """Open an MCP ClientSession over stdio (one-shot subprocess)."""
    command = _stdio_python()
    server_script = HIVE_INSTALL_DIR / "lib" / "hive_mcp" / "server.py"
    if not server_script.is_file():
        raise RuntimeError(
            f"MCP server script not found at {server_script} "
            f"(HIVE_INSTALL_DIR={HIVE_INSTALL_DIR})")
    params = StdioServerParameters(
        command=command,
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
    """Pick a transport (HTTP if up, else stdio) and call the tool.

    SDK-backed when the mcp SDK is importable; otherwise the shared
    SDK-free HTTP client (C.2). The SDK-free path serves the HTTP
    transport only — stdio needs the SDK to spawn the child session.
    """
    if not _HAS_MCP_SDK:
        if not _is_http_server_up():
            return {"ok": False,
                    "error": "MCP HTTP server not reachable and the mcp SDK "
                             "is not installed (stdio fallback unavailable)"}
        res = _load_sdk_free_client().call_tool(_http_endpoint(), name, arguments)
        if not res.get("ok"):
            return res
        text = res.get("response", "")
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return {"ok": True, "stdout": text}

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
    if not _HAS_MCP_SDK:
        if not _is_http_server_up():
            return []
        return sorted(_load_sdk_free_client().list_tools(_http_endpoint()))
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
