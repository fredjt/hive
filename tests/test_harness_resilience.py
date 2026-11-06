"""F5/F6 (2026-11-06 field run): LLM endpoint resilience.

The test company's power went out mid-sprint, killing the LLM server while
the laptop running hive stayed up. Two hive-side weaknesses surfaced:

F5: `hive llm test` sent max_tokens=16. Thinking models (Qwen3 family)
spend that budget on their reasoning prefix and come back with EMPTY
content — a perfectly healthy endpoint then produced a misleading
"empty reply - endpoint answered but returned no content" warning at
startup while every subsequent agent call worked fine.

F6: the harness LLM retry used one 120s timeout for connect+read and NO
backoff after timeouts. After the outage (endpoint black-holing), the
first failed spawn took ~5 minutes before it was even counted by the
outage circuit breaker. Now: short connect timeout, long read/generation
timeout (both env-overridable), and backoff between timed-out attempts.
"""
import importlib.util
import json
import os
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load_harness():
    """agent-harness.py has a dash — load it by file path."""
    spec = importlib.util.spec_from_file_location(
        "hive_agent_harness", REPO / "lib" / "core" / "agent-harness.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["hive_agent_harness"] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_llm_cmd():
    spec = importlib.util.spec_from_file_location(
        "hive_llm_cmd", REPO / "lib" / "hive_cli" / "commands" / "llm.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["hive_llm_cmd"] = mod
    spec.loader.exec_module(mod)
    return mod


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _ThinkingStubHandler(BaseHTTPRequestHandler):
    """F5: simulates a thinking model. If the requested max_tokens is too
    small, the reasoning prefix consumes the whole budget and `content`
    comes back empty (exactly what the Qwen3.6 endpoint did with 16)."""

    payloads = []

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        _ThinkingStubHandler.payloads.append(body)
        budget = int(body.get("max_tokens", 0))
        content = "" if budget < 64 else "OK"
        payload = json.dumps({
            "model": "stub",
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": min(budget, 8),
                      "total_tokens": 18},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *a):
        pass


def test_llm_test_budget_survives_thinking_models(tmp_path, monkeypatch, capsys):
    """F5: the preflight must request enough tokens for a reasoning prefix,
    or thinking models report a healthy endpoint as an 'empty reply'."""
    port = _free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), _ThinkingStubHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _ThinkingStubHandler.payloads.clear()
    try:
        monkeypatch.setenv("HIVE_PROVIDER", "openai")
        monkeypatch.setenv("HIVE_API_KEY", "test")
        monkeypatch.setenv("HIVE_MODEL", "stub")
        monkeypatch.setenv("HIVE_API_BASE", f"http://127.0.0.1:{port}/v1")
        monkeypatch.setenv("COMPANY_DIR", str(tmp_path))
        llm_cmd = _load_llm_cmd()
        rc = llm_cmd._llm_test()
    finally:
        server.shutdown()
    out = capsys.readouterr().out
    assert _ThinkingStubHandler.payloads, "no LLM request was sent"
    budget = int(_ThinkingStubHandler.payloads[-1].get("max_tokens", 0))
    assert budget >= 64, (
        f"llm test requested max_tokens={budget} — a thinking model's "
        f"reasoning prefix consumes that and returns empty content")
    assert rc == 0, f"llm test failed: {out}"
    assert "empty reply" not in out, (
        f"healthy endpoint flagged as empty reply: {out}")


class _HangingHandler(BaseHTTPRequestHandler):
    """F6: accepts the TCP connection but never answers — a black-holing
    endpoint (what a LAN looks like right after the remote box loses
    power)."""

    def do_POST(self):
        time.sleep(30)

    def log_message(self, *a):
        pass


def test_llm_retry_bounded_with_env_timeouts(monkeypatch):
    """F6: a dead endpoint must fail in bounded time with backoff — not
    burn the full default 120s per attempt (the field run's first failed
    spawn took 308s before counting toward the outage breaker)."""
    if os.environ.get("HIVE_NO_NETWORK_TESTS"):
        import pytest
        pytest.skip("network tests disabled")
    port = _free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), _HangingHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv("HIVE_LLM_CONNECT_TIMEOUT", "1")
        monkeypatch.setenv("HIVE_LLM_READ_TIMEOUT", "2")
        harness = _load_harness()
        t0 = time.monotonic()
        result = harness.call_llm_api(
            [{"role": "user", "content": "ping"}],
            api_base=f"http://127.0.0.1:{port}/v1",
            model="stub", retries=3)
        elapsed = time.monotonic() - t0
    finally:
        server.shutdown()
    assert result.get("ok") is False, result
    err = result.get("error", "")
    assert "after 3 attempts" in err, err
    # 3 read timeouts (2s each) + backoff sleeps (1s + 2s) => ~9s.
    # Pre-F6 this path had no env override and no timeout backoff: with
    # defaults it would take ~6 minutes.
    assert elapsed < 45, f"retry loop took {elapsed:.0f}s — not bounded"
    assert elapsed >= 3.0, (
        f"no backoff between timed-out attempts (elapsed {elapsed:.1f}s)")
