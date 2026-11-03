"""C.1: the harness tool-loop contract, behavior-tested end to end.

Stub LLM endpoint (a canned 3-turn script: call a write tool, call a read
tool, answer) + the REAL MCP server over HTTP on an ephemeral port + the
REAL harness (run as a subprocess, exactly as agent-spawn.sh runs it) ->
assert the write landed in a scratch .company, the read returned it into
the conversation, and the stream log carries the tool events.

This is the integration test the wire path never had: before C.1 the
suite only exercised in-process tool registration and the CLI's own
mcp_client, never the harness's own JSON-RPC over HTTP inside the
conversation loop.
"""
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

TICKET_DEPT = "it"
TICKET_TITLE = "C1 harness tool-loop probe"


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_port(port: int, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def _free_http_port_and_start(factory):
    """Start a ThreadingHTTPServer with the given handler class on a fresh
    ephemeral port; returns (base_url, server)."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), factory)
    import threading
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}", server


class _TurnLLMHandler(BaseHTTPRequestHandler):
    """Canned 3-turn LLM: turn 1 calls ticket_create, turn 2 calls
    ticket_get (ticket id pulled from the tool result the harness
    injected into the conversation), turn 3 gives the final answer.

    Records every request body so the test can assert what the harness
    actually sent (including the read tool's result in turn 3)."""

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n).decode("utf-8")
        self.server.requests.append(body)
        self.server.turn += 1
        turn = self.server.turn
        if turn == 1:
            content = (
                "```mcp: ticket_create(dept=\"" + TICKET_DEPT + "\", "
                "type=\"chore\", priority=\"low\", title=\"" + TICKET_TITLE +
                "\", description=\"C.1 behavioral round-trip\")```"
            )
        elif turn == 2:
            m = re.search(r"([A-Z]+-\d{4})", body)
            if not m:
                # Fail loudly: the harness must have injected the create
                # result (which carries the ticket id) into the prompt.
                payload = json.dumps({"model": "stub",
                                      "choices": [{"message": {"content": "NO-TICKET-ID-VISIBLE"}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            ticket_id = m.group(1)
            content = (
                "```mcp: ticket_get(dept=\"" + TICKET_DEPT + "\", "
                "ticket_id=\"" + ticket_id + "\")```"
            )
        else:
            content = "Done: ticket created and read back."
        payload = json.dumps({"model": "stub",
                              "choices": [{"message": {"content": content}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):  # keep test output quiet
        pass


@pytest.fixture
def mcp_http_server(tmp_path):
    """Boot the REAL MCP server (`server.py --transport http`) on an
    ephemeral port — the same command `hive start` runs — pointed at a
    scratch .company (so tool calls can never touch the repo's live
    state). Yields (mcp_url, company_dir). Terminates on teardown."""
    company_dir = tmp_path / "company"
    (company_dir / "departments").mkdir(parents=True)
    port = _free_local_port()
    env = {
        **os.environ,
        "PYTHONPATH": str(REPO / "lib"),
        "HIVE_ROOT": str(REPO),
        "HIVE_INSTALL_DIR": str(REPO),
        "COMPANY_DIR": str(company_dir),
    }
    log_path = tempfile.mktemp(prefix="hive-mcp-c1-", suffix=".log")
    log_file = open(log_path, "w")
    proc = subprocess.Popen(
        [sys.executable, str(REPO / "lib" / "hive_mcp" / "server.py"),
         "--transport", "http", "--host", "127.0.0.1", "--port", str(port)],
        env=env, cwd=str(tmp_path),
        stdout=log_file, stderr=subprocess.STDOUT,
    )
    try:
        if not _wait_for_port(port, timeout=30.0):
            proc.kill()
            log_file.close()
            try:
                log = Path(log_path).read_text()
            except OSError:
                log = "(log unreadable)"
            pytest.fail(f"MCP HTTP server did not open port {port}; log:\n{log}")
        yield f"http://127.0.0.1:{port}/mcp", str(company_dir)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log_file.close()
        try:
            os.unlink(log_path)
        except OSError:
            pass


def test_tool_loop_round_trip(mcp_http_server):
    """C.1: write tool lands on disk, read tool returns it, stream log
    carries the tool events."""
    mcp_url, company_dir = mcp_http_server

    llm_base, llm_server = _free_http_port_and_start(_TurnLLMHandler)
    llm_server.requests = []
    llm_server.turn = 0
    try:
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(Path(company_dir).parent / "home"),
            "HIVE_ROOT": str(REPO),
            "COMPANY_DIR": company_dir,
            "MCP_URL": mcp_url,
            "HIVE_PROVIDER": "openai",
            "HIVE_API_BASE": llm_base,
            "HIVE_MODEL": "stub",
            "HIVE_API_KEY": "test-key",
        }
        r = subprocess.run(
            [sys.executable, str(REPO / "lib" / "core" / "agent-harness.py"),
             "--", "Create the ticket, read it back, then finish."],
            capture_output=True, text=True, timeout=120, env=env,
            cwd=str(Path(company_dir).parent),
        )
        assert r.returncode == 0, f"harness failed: {r.stderr[-800:]}"

        # (a) the write really landed in the scratch .company
        tickets_root = Path(company_dir) / "departments" / TICKET_DEPT / "tickets"
        found = list(tickets_root.rglob("*.json"))
        found = [f for f in found if f.name != "counter.json"]
        assert len(found) == 1, f"expected exactly one ticket, got {found}"
        data = json.loads(found[0].read_text())
        assert data["title"] == TICKET_TITLE
        assert data["status"] == "backlog"
        ticket_id = data["id"]

        # (b) the read returned the write: turn 3's prompt carried the
        # ticket_get result, which contains the title and the id
        assert len(llm_server.requests) == 3, \
            f"expected 3 LLM turns, got {len(llm_server.requests)}"
        turn3 = llm_server.requests[2]
        assert TICKET_TITLE in turn3, "read result missing from turn-3 prompt"
        assert ticket_id in turn3, "ticket id missing from turn-3 prompt"

        # (c) the stream log (harness stdout) carries the tool events
        events = [json.loads(line) for line in r.stdout.strip().splitlines()
                  if line.strip().startswith("{")]
        tool_uses = [e for e in events if e.get("type") == "tool_use"]
        tool_results = [e for e in events if e.get("type") == "tool_result"]
        assert [e["tool"] for e in tool_uses] == ["ticket_create", "ticket_get"], \
            f"tool_use events wrong: {tool_uses}"
        assert [e["tool"] for e in tool_results] == ["ticket_create", "ticket_get"]
        assert all(e.get("ok") for e in tool_results), \
            f"a tool result failed: {tool_results}"
        assert any(e.get("type") == "assistant" for e in events)
        final = events[-1]
        assert final["type"] == "result"
        assert final["is_error"] is False
        assert "created and read back" in final["result"]
    finally:
        llm_server.shutdown()
        llm_server.server_close()
