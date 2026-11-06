"""Item 8: mcp_http.call_tool must honor the MCP isError flag.

The field run's 12 failed calls (mcp__hive__<tool> names) surfaced as
"RESULT ok=True: Unknown tool: ..." - the client ignored isError and
reported success with the error text as the response, misleading both
the model and the operator logs.
"""
import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load_mcp_http():
    path = REPO / "lib" / "core" / "mcp_http.py"
    spec = importlib.util.spec_from_file_location("mcp_http_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _client_with_response(body):
    m = _load_mcp_http()
    client = m.McpHttpClient.__new__(m.McpHttpClient)
    client._client = None
    client._session_id = None
    client._post = lambda payload: body
    return m, client


def test_iserror_tool_result_is_not_ok():
    body = {"jsonrpc": "2.0", "id": 2,
            "result": {"content": [
                {"type": "text", "text": "Unknown tool: mcp__hive__ticket_get"}
            ], "isError": True}}
    _, client = _client_with_response(body)
    out = client.call_tool("ticket_get", {})
    assert out["ok"] is False
    assert "Unknown tool" in out["error"]
    assert "response" not in out


def test_jsonrpc_error_is_not_ok():
    body = {"jsonrpc": "2.0", "id": 2,
            "error": {"code": -32601, "message": "Method not found"}}
    _, client = _client_with_response(body)
    out = client.call_tool("ticket_get", {})
    assert out["ok"] is False
    assert "Method not found" in out["error"]


def test_normal_tool_result_still_ok():
    body = {"jsonrpc": "2.0", "id": 2,
            "result": {"content": [{"type": "text", "text": "ticket payload"}],
                       "isError": False}}
    _, client = _client_with_response(body)
    out = client.call_tool("ticket_get", {})
    assert out["ok"] is True
    assert out["response"] == "ticket payload"
