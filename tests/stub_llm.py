"""tests/stub_llm.py — a local stub LLM endpoint for tests.

A threaded http.server that answers POST /chat/completions (OpenAI wire
format) without touching the network. Tests drive it with a *responder*
callback: ``responder(request_body: dict) -> dict`` returning the
completion body. Every request is recorded in ``.requests`` (list of
parsed bodies) so tests can assert on what the harness actually asked.

The default responder returns one final assistant message (no bash:/mcp:
blocks) with a deterministic usage block, which is all B1 needs: one
call, one usage-log entry, a computable cost.
"""

from __future__ import annotations

import http.server
import json
import threading
import time


def default_responder(body: dict) -> dict:
    """One-shot final answer with a fixed usage block.

    model "gpt-4o" is priced in llm_providers (the gpt-4 branch), so
    parse_response_body computes total_cost_usd =
    (100 * 0.00003 + 50 * 0.00006) / 1000 = 6e-06.
    """
    return {
        "id": "chatcmpl-stub",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "gpt-4o",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": "Done. (stub)"},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
    }


class StubLLM:
    """Run a stub /chat/completions endpoint on an ephemeral 127.0.0.1 port.

    Usage:
        stub = StubLLM()                 # default responder
        stub = StubLLM(my_responder)     # scripted per request
        harness --api-base stub.base_url ...
        stub.requests   # list of request bodies, in order
        stub.stop()
    """

    def __init__(self, responder=None, fail_status=None):
        self.responder = responder or default_responder
        self.requests: list[dict] = []
        self.failures: list[str] = []
        # A2: when set, every request answers with this HTTP status and an
        # error body (checked per request, so a test can clear it at
        # runtime to simulate the endpoint recovering).
        self.fail_status = fail_status
        outer = self

        class _Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                try:
                    length = int(self.headers.get("Content-Length", 0))
                    body = json.loads(self.rfile.read(length) or b"{}")
                    outer.requests.append(body)
                    if outer.fail_status is not None:
                        err = json.dumps({"error": {
                            "message": "stub endpoint failure",
                            "type": "authentication_error",
                        }}).encode("utf-8")
                        self.send_response(outer.fail_status)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(err)))
                        self.end_headers()
                        self.wfile.write(err)
                        return
                    resp = outer.responder(body)
                    payload = json.dumps(resp).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                except Exception as exc:  # never kill the serve loop
                    outer.failures.append(f"{type(exc).__name__}: {exc}")
                    self.send_response(500)
                    self.send_header("Content-Length", "2")
                    self.end_headers()
                    self.wfile.write(b"{}")

            def log_message(self, *a):  # keep test output quiet
                pass

        class _Server(http.server.ThreadingHTTPServer):
            allow_reuse_address = True
            daemon_threads = True

        self._server = _Server(("127.0.0.1", 0), _Handler)
        self.port = self._server.server_address[1]
        self.base_url = f"http://127.0.0.1:{self.port}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        try:
            self._server.shutdown()
            self._server.server_close()
        except Exception:
            pass
