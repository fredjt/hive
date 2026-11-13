"""Client for the hive MCP server: HTTP transport, in-process execution.

Transport ladder (T1, 0.22 — the in-process rung is SDK-FREE by
construction; the 0.20 rung was the right design but build_server()
paid the SDK import it never used, so a stopped read cost 1.1 s of
which 0.96 s was `import mcp`, twice):
  1. Streamable HTTP — the fast path, used whenever the MCP HTTP
     server is up (a running company): the official mcp SDK client
     when the SDK is importable, otherwise the shared SDK-free
     client (lib/core/mcp_http.py, httpx only — C.2).
  2. In-process — the server is down: the requested tool module is
     imported lazily (ONE module per command, no build_server() sweep)
     and its `register()` is pointed at a duck-typed collector that
     records `tool name -> fn` — the very function object the SDK
     server would wrap, called with wire-mirroring argument
     filtering (unknown keys dropped), so the envelope is
     byte-identical across all three rungs: one implementation, no
     transport, no SDK import, no spawn.
  3. The house error envelope — the module import itself fails or no
     module provides the name: a real defect now, not the default
     path; the error shape stays, the rung is named.

The stdio spawn retired from the CLI in 0.20: the one-shot
`server.py --transport stdio` child (and its per-spawn venv NOTE
line) is gone — every stopped-company read used to pay a second
`import mcp` + build_server() + a JSON-RPC handshake in the child
before the work began. The stdio SERVER transport
(`python -m hive_mcp.server --transport stdio`) STAYS for its
programmatic consumers (scripts, tests, interactive sessions — the
AGENTS.md contract); only the CLI stops being one.

The mcp SDK is needed ONLY for the HTTP client choice (rung 1 when
the server is up): importing this module does not import the SDK,
non-tool commands never pay it, and the stopped read (rung 2) no
longer requires it at all (T1, 0.22 — the severance of the SDK from
the tool modules; the guard in tests/test_xdist_contract.py,
TestToolModulesSdkFree, fails the suite on a new server_compat import
in a tool module).
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

    T1 (0.22): the SDK is consulted ONLY for the HTTP client choice
    (rung 1 — the SDK client when the server is up, the shared
    SDK-free client otherwise). The in-process rung (rung 2) is
    SDK-free by construction and never resolves this. Lazy: resolved
    once per process on first use, cached (the answer cannot change).
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


class _ToolCollector:
    """Duck-typed stand-in for the SDK server on the in-process rung.

    The tool modules only ever call ``server.tool()`` (as a decorator)
    and reference the returned function — they never construct or
    inspect the SDK object (the ``Server`` annotation on ``register``
    is a string under ``from __future__ import annotations``). A
    collector that records ``fn.__name__ -> fn`` is indistinguishable
    to them, which is what makes the rung SDK-free by construction:
    the functions recorded here are the very objects the SDK server
    would wrap, so the envelope is byte-identical by construction (one
    implementation, no transport, no SDK)."""

    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self, *args: Any, **kwargs: Any):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn
        return decorator


#: T1 (0.22) in-process registries — per tool module, built once per
#: CLI process (the tool functions are process-stable).
_MODULE_REGISTRIES: dict[str, dict[str, Any]] = {}


def _module_registry(mod_name: str) -> dict[str, Any]:
    """The ``{tool name: fn}`` of one tool module, built by pointing its
    ``register()`` at the collector — NO SDK import anywhere in the
    chain (the modules import only stdlib + hive_mcp.shell; the
    severance is T1, 0.22). A failing import is a real defect and is
    surfaced, not swallowed."""
    reg = _MODULE_REGISTRIES.get(mod_name)
    if reg is None:
        import importlib
        lib_dir = str(Path(__file__).resolve().parent.parent)
        if lib_dir not in sys.path:
            sys.path.insert(0, lib_dir)
        mod = importlib.import_module(f"hive_mcp.tools.{mod_name}")
        collector = _ToolCollector()
        if hasattr(mod, "register"):
            mod.register(collector)
        reg = collector.tools
        _MODULE_REGISTRIES[mod_name] = reg
    return reg


def _resolve_tool_fn(name: str) -> Any:
    """Lazy per-command import (T1, 0.22): walk the tool modules until
    ``name`` resolves — the command pays for the ONE module its tool
    lives in (early exit), never a build_server() sweep, never the
    SDK. Returns None when no module provides the name; a module
    import failure PROPAGATES (the caller names it as the rung's
    error envelope — a real defect, not the default path)."""
    from hive_mcp.tools import TOOL_MODULES
    for mod_name in TOOL_MODULES:
        if name in _module_registry(mod_name):
            return _module_registry(mod_name)[name]
    return None


def _call_in_process(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """The middle rung (T1, 0.22): run the tool function in THIS
    process — SDK-free by construction.

    No transport, no SDK import, no dead process — the stopped
    company's read costs the tool's own work (hive_state + the
    stdlib shell runner). The functions are the codebase's own (all
    tool functions are sync and return their dict envelope directly).
    Argument filtering mirrors the WIRE: the transport validates
    against the tool's parameter schema (pydantic, extra ignored —
    unknown keys are dropped, e.g. the CLI's historical `force` key on
    ticket_move), so the same payload behaves identically on all three
    rungs."""
    try:
        fn = _resolve_tool_fn(name)
    except Exception as e:
        return {"ok": False,
                "error": f"in-process tool resolution failed: "
                         f"{type(e).__name__}: {e}"}
    if fn is None:
        return {"ok": False,
                "error": f"in-process tool resolution failed: no tool "
                         f"module provides {name!r}"}
    params = inspect.signature(fn).parameters
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return fn(**arguments)
    return fn(**{k: v for k, v in arguments.items() if k in params})


def _list_in_process() -> list[str]:
    """The tool names, in-process (T1, 0.22) — every module's collector
    registry, merged and sorted. SDK-free: the collector is duck-typed,
    so the list no longer needs build_server() (or the SDK) at all."""
    from hive_mcp.tools import TOOL_MODULES
    names: set[str] = set()
    for mod_name in TOOL_MODULES:
        names.update(_module_registry(mod_name))
    return sorted(names)


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
    """Walk the transport ladder (T1, 0.22) and call the tool:

      1. HTTP if the server is up — the fast path, unchanged: the SDK
         client when the SDK is importable, the shared SDK-free client
         otherwise (C.2).
      2. In-process when the server is down — the tool's own function,
         no transport, no SDK (SDK-free by construction; the module is
         imported lazily, one per command).
      3. The house error envelope when the module import itself fails
         or no module provides the name — the shape stays, the rung is
         named (a real defect now, not the default path).
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

    # Rung 2: in-process, SDK-free by construction (T1, 0.22) — the
    # stopped read costs the tool's own work, never `import mcp`.
    return _call_in_process(name, arguments)


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
    """The tool names: over the wire when the server is up (the SDK
    client when the SDK is importable, the shared SDK-free client
    otherwise), the in-process registry when it is down (T1, 0.22:
    one path, no spawn — and the down rung is SDK-free by
    construction, so a no-SDK environment lists just as well)."""
    if _is_http_server_up():
        if _sdk_available():
            try:
                return asyncio.run(_list_http_async())
            except Exception:
                return _list_in_process()
        return sorted(_load_sdk_free_client().list_tools(_http_endpoint()))
    try:
        return _list_in_process()
    except Exception:
        # A failing tool module is a real defect — name nothing rather
        # than crash the caller (the rung is named in call_tool's
        # error envelope for the specific tool).
        return []
