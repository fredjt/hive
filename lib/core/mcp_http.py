"""SDK-free Streamable HTTP MCP client (httpx + stdlib only).

Shared protocol layer for the hive MCP server (C.2 — "one MCP client, two
consumers"). The agent harness runs under plain python3 with no mcp SDK
installed, so its MCP path cannot import the SDK; the CLI keeps the SDK as
a fast path but both consumers use this same protocol implementation for
the wire details:

    initialize -> (mcp-session-id header) -> notifications/initialized
    -> tools/call | tools/list

and parse both application/json and text/event-stream (SSE) response
bodies.
"""

from __future__ import annotations

import json
from typing import Any

try:
    import httpx
except ImportError:  # pragma: no cover - httpx is a runtime dep
    httpx = None  # type: ignore


def parse_response_body(resp: "httpx.Response") -> Any:
    """Parse a JSON-RPC response body that may be plain JSON or SSE
    (Streamable HTTP servers answer ``event: message`` / ``data: {...}``).

    Returns the decoded JSON-RPC object, or None when unparseable.
    """
    ctype = resp.headers.get("content-type", "") or ""
    if "text/event-stream" in ctype:
        for line in resp.text.splitlines():
            if line.startswith("data: "):
                try:
                    return json.loads(line[6:])
                except json.JSONDecodeError:
                    continue
        return None
    try:
        return resp.json()
    except json.JSONDecodeError:
        return None


def _base_headers() -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }


class McpHttpClient:
    """A Streamable HTTP MCP session: initialize handshake, then
    tools/call and tools/list over the session the server issues.

    Usage::

        with McpHttpClient("http://127.0.0.1:7891/mcp") as mcp:
            result = mcp.call_tool("ticket_get", {"dept": "it", ...})
            names = mcp.list_tools()
    """

    def __init__(self, endpoint: str, timeout: float = 30.0):
        if httpx is None:
            raise RuntimeError("httpx not installed — MCP tool calls disabled")
        self.endpoint = endpoint
        self.timeout = timeout
        self._client: "httpx.Client | None" = None
        self._session_id: str | None = None

    def connect(self) -> None:
        """initialize handshake; store the mcp-session-id for later calls."""
        self._client = httpx.Client(timeout=self.timeout)
        headers = _base_headers()
        init_resp = self._client.post(
            self.endpoint,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "clientInfo": {"name": "hive-mcp-http-client", "version": "1.0"},
                },
            },
            headers=headers,
        )
        if init_resp.status_code != 200:
            raise RuntimeError(f"MCP init failed: {init_resp.status_code}")
        # The server issues a session id that must ride on every later
        # request (without it tools/call fails with 400 "Missing session ID").
        self._session_id = init_resp.headers.get("mcp-session-id")
        if self._session_id:
            # Spec: the client must acknowledge initialization.
            self._client.post(
                self.endpoint,
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                headers={**headers, "mcp-session-id": self._session_id},
            )

    def _headers(self) -> dict[str, str]:
        h = _base_headers()
        if self._session_id:
            h["mcp-session-id"] = self._session_id
        return h

    def _post(self, payload: dict) -> Any:
        assert self._client is not None
        resp = self._client.post(self.endpoint, json=payload, headers=self._headers())
        if resp.status_code != 200:
            raise RuntimeError(f"MCP call failed: {resp.status_code}")
        body = parse_response_body(resp)
        if body is None:
            raise RuntimeError("MCP call returned an unparseable body")
        return body

    @staticmethod
    def extract_text(result: Any) -> str:
        """Pull the text payload out of a JSON-RPC tools response."""
        if isinstance(result, dict) and "result" in result:
            content = result["result"]
        else:
            content = result
        texts = []
        if isinstance(content, dict):
            for block in content.get("content", []):
                if isinstance(block, dict) and block.get("type") == "text":
                    texts.append(block.get("text", ""))
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    texts.append(block.get("text", ""))
        return "\n".join(texts) if texts else str(content)

    def call_tool(self, tool_name: str, arguments: dict) -> dict[str, Any]:
        """Call one tool; returns {ok, tool, arguments, response|error}."""
        result = self._post({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        })
        return {
            "ok": True,
            "tool": tool_name,
            "arguments": arguments,
            "response": self.extract_text(result),
        }

    def list_tools(self) -> list[str]:
        """Return the sorted names of all registered tools."""
        result = self._post({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/list",
            "params": {},
        })
        if isinstance(result, dict) and "result" in result:
            tools = result["result"].get("tools", [])
        else:
            tools = []
        return sorted(t.get("name", "") for t in tools if isinstance(t, dict))

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> "McpHttpClient":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def call_tool(endpoint: str, tool_name: str, arguments: dict,
              timeout: float = 30.0) -> dict[str, Any]:
    """One-shot convenience wrapper: connect, call, close.

    Mirrors the harness's historical call_mcp_tool() return shape so
    callers can treat a connection error and an unparseable body the
    same way.
    """
    try:
        with McpHttpClient(endpoint, timeout=timeout) as mcp:
            return mcp.call_tool(tool_name, arguments)
    except Exception as e:  # noqa: BLE001 - surfaced as a clean dict
        return {
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "tool": tool_name,
        }


def list_tools(endpoint: str, timeout: float = 30.0) -> list[str]:
    """One-shot convenience wrapper: connect, list, close."""
    with McpHttpClient(endpoint, timeout=timeout) as mcp:
        return mcp.list_tools()
