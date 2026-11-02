"""Compatibility layer for mcp server API (v1.x and v2.x).

In mcp 1.x: from mcp.server.fastmcp import FastMCP
In mcp 2.x: from mcp.server.mcpserver import MCPServer (FastMCP renamed)
"""

try:
    # mcp >= 2.0
    from mcp.server.mcpserver import MCPServer as _ServerClass
except ImportError:
    # mcp < 2.0
    from mcp.server.fastmcp import FastMCP as _ServerClass

# Export the class under both names for compatibility
Server = _ServerClass
FastMCP = _ServerClass
MCPServer = _ServerClass


def _settings_have_host_port(settings) -> bool:
    """mcp 1.x (FastMCP) keeps host/port on the settings model; mcp 2.x
    (MCPServer) removed them — run() takes them as **kwargs instead."""
    fields = getattr(type(settings), "model_fields", None)
    return bool(fields) and "host" in fields and "port" in fields


def run_http(server, host: str, port: int) -> None:
    """Run a built server on the streamable-http transport, on both mcp
    1.x (FastMCP: host/port come from server.settings) and mcp 2.x
    (MCPServer: run() forwards **kwargs to the streamable-http runner).

    Calling `server.settings.host = ...` on mcp 2.x raises
    `ValueError: "Settings" object has no field "host"` and the HTTP
    transport — the one the CLI and dashboard actually use — never starts.
    """
    if _settings_have_host_port(server.settings):
        server.settings.host = host
        server.settings.port = port
        server.run(transport="streamable-http")
    else:
        server.run(transport="streamable-http", host=host, port=port)
