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
