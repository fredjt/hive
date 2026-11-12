"""Client for the hive MCP server: HTTP transport, in-process execution.

Transport ladder (T1, 0.20 — the stopped company answers in-process;
the dead stdio process is retired from the CLI):
  1. Streamable HTTP — the fast path, used whenever the MCP HTTP
     server is up (a running company): the official mcp SDK client
     when the SDK is importable, otherwise the shared SDK-free
     client (lib/core/mcp_http.py, httpx only — C.2).
  2. In-process — the SDK is importable and the server is down:
     build_server() (built once per CLI process, cached) +
     registry[name].fn(**arguments) — the very function object the
     server exposes, so the envelope is byte-identical to the
     transport by construction: one implementation, no transport,
     no second SDK import, no spawn.
  3. The house error envelope — no SDK, no server: the error shape
     stays, the rung is named ("in-process fallback unavailable").

The stdio spawn retired from the CLI in 0.20: the one-shot
`server.py --transport stdio` child (and its per-spawn venv NOTE
line) is gone — every stopped-company read used to pay a second
`import mcp` + build_server() + a JSON-RPC handshake in the child
before the work began. The stdio SERVER transport
(`python -m hive_mcp.server --transport stdio`) STAYS for its
programmatic consumers (scripts, tests, interactive sessions — the
AGENTS.md contract); only the CLI stops being one.

The mcp SDK import is LAZY (T1, 0.20): importing this module does
not import the SDK. Non-tool commands (version, --help, stop,
killall, config) never pay the SDK import — it is resolved once per
process on the first tool call or list.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx

# G9: install root resolved once in lib/hive_paths.py. HIVE_ROOT is the
# global install root (tools find their lib/tools/ here); HIVE_INSTALL_DIR
# is an alias kept for existing readers.
from hive_paths import install_root

HIVE_ROOT = install_root()
HIVE_INSTALL_DIR = HIVE_ROOT
MCP_HOST = os.environ.get("HIVE_MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.environ.get("HIVE_MCP_PORT", "7891"))


def _http_endpoint() -> str:
    return f"http://{MCP_HOST}:{MCP_PORT}/mcp"


def _sdk_available() -> bool:
    """Is the mcp SDK importable in this process?

    T1 (0.20): the SDK import is lazy — this resolves on the first
    tool call or list, not on this module's import, so non-tool
    commands (version, --help, stop, killall, config) never pay it.
    Resolved once per process, cached (the answer cannot change).
    """
    global _sdk_state
    if _sdk_state is None:
        try:
            import mcp  # noqa: F401
            _sdk_state = True
        except ImportError:  # SDK-free environment (C.2)
            _sdk_state = False
    return _sdk_state


#: T1 (0.20) lazy-SDK state — None until first resolution.
_sdk_state: bool | None = None


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
    """Open an MCP ClientSession over Streamable HTTP (the SDK client —
    only reached when _sdk_available() is True, so the imports inside
    are the first SDK imports of the process)."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    # mcp 1.x yields (read_stream, write_stream, session_manager);
    # mcp 2.x yields just (read_stream, write_stream). Index into the tuple
    # so both SDK generations work — unpacking 3 names on 2.x raises
    # ValueError inside the TaskGroup and kills every HTTP tool call.
    async with streamable_http_client(_http_endpoint()) as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            yield session


_server_instance = None


def _in_process_server():
    """build_server() ONCE per CLI process (T1, 0.20 middle rung).

    The registry the HTTP transport serves IS this server's registry —
    in-process execution calls the very function objects FastMCP
    wraps, so the envelope is byte-identical by construction (one
    implementation, no transport)."""
    global _server_instance
    if _server_instance is None:
        lib_dir = str(Path(__file__).resolve().parent.parent)
        if lib_dir not in sys.path:
            sys.path.insert(0, lib_dir)
        from hive_mcp.server import build_server
        _server_instance = build_server()
    return _server_instance


def _call_in_process(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """The middle rung (T1, 0.20): run the tool function in THIS process.

    No transport, no second SDK import, no dead process — the stopped
    company's read costs the tool's own work. The registry is the
    codebase's own (all tool functions are sync and return their
    dict envelope directly). Argument filtering mirrors the WIRE: the
    transport validates against the tool's parameter schema (pydantic,
    extra ignored — unknown keys are dropped, e.g. the CLI's historical
    `force` key on ticket_move), so the same payload behaves
    identically on both rungs."""
    fn = _in_process_server()._tool_manager._tools[name].fn
    params = inspect.signature(fn).parameters
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return fn(**arguments)
    return fn(**{k: v for k, v in arguments.items() if k in params})


def _list_in_process() -> list[str]:
    """The registry's tool names, in-process (T1, 0.20) — the same
    build_server()._tool_manager._tools.keys() the except branch of
    list_tools() always did, as the named rung instead of the
    accident."""
    return sorted(_in_process_server()._tool_manager._tools.keys())


def _unwrap_result(result) -> dict[str, Any]:
    """The SDK call result's payload — structuredContent first (FastMCP
    wraps single-dict returns under a "result" key), the first content
    text second, the isError envelope last."""
    out = result.structuredContent if hasattr(result, "structuredContent") and result.structuredContent else None
    if out:
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


async def _call_async(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Walk the transport ladder (T1, 0.20) and call the tool:

      1. HTTP if the server is up — the fast path, unchanged: the SDK
         client when the SDK is importable, the shared SDK-free client
         otherwise (C.2).
      2. In-process when the server is down and the SDK is importable
         — the tool's own function, no transport.
      3. The house error envelope when there is neither — the shape
         stays, the rung is named.
    """
    if _is_http_server_up():
        if _sdk_available():
            async with _http_session() as session:
                result = await session.call_tool(name, arguments=arguments)
            return _unwrap_result(result)
        res = _load_sdk_free_client().call_tool(_http_endpoint(), name, arguments)
        if not res.get("ok"):
            return res
        text = res.get("response", "")
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return {"ok": True, "stdout": text}

    if _sdk_available():
        return _call_in_process(name, arguments)

    return {"ok": False,
            "error": "MCP HTTP server not reachable and the mcp SDK "
                     "is not installed (in-process fallback unavailable)"}


def call_tool(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """Sync wrapper around the async MCP call."""
    arguments = arguments or {}
    try:
        return asyncio.run(_call_async(name, arguments))
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


async def _list_http_async() -> list[str]:
    async with _http_session() as session:
        resp = await session.list_tools()
        return sorted(t.name for t in resp.tools)


def list_tools() -> list[str]:
    """The tool names: HTTP when the server is up, the in-process
    registry when it is down (T1, 0.20: one path, no spawn). SDK-free
    environments list over the wire when the server is up and name
    nothing when it is down (the rung below them needs the SDK)."""
    if not _sdk_available():
        if not _is_http_server_up():
            return []
        return sorted(_load_sdk_free_client().list_tools(_http_endpoint()))
    try:
        if _is_http_server_up():
            return asyncio.run(_list_http_async())
        return _list_in_process()
    except Exception:
        # Last resort: introspect the server module directly.
        sys.path.insert(0, str(HIVE_ROOT / "lib"))
        from hive_mcp.server import build_server  # noqa: E402
        server = build_server()
        return sorted(server._tool_manager._tools.keys())
