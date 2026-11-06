"""E3 (release goal 0.10.0): a killed spawn is logged as killed.

agent-spawn.sh's EXIT trap used to write "done" to cycles.jsonl
unconditionally — the 0.10.0 field run's SIGTERMed vp-it is
indistinguishable from a completed run in the log the dashboard and
every post-mortem read. Now: clean exit -> "done", non-zero harness
exit -> "failed", death by signal -> "killed" with the signal name.
"""

import json
import os
import shlex
import signal
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SPAWN = REPO / "lib" / "core" / "agent-harness.py"
SPAWN_SH = REPO / "lib" / "core" / "agent-spawn.sh"


class _Responder(BaseHTTPRequestHandler):
    """Turn 1 = a single bash action; afterwards the conversation ends.

    Mode is a CLASS attribute set by each test before its server starts
    (handler instances can't see per-test server locals otherwise).
    """

    mode = "done"

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(self.respond(body)).encode())

    def respond(self, body):
        text = "\n".join(
            m.get("content", "") for m in body.get("messages", [])
            if isinstance(m.get("content"), str))
        if _Responder.mode == "sleep":
            return self._final("```bash: sleep 4```")
        if "Action results:" in text:
            return self._final("Done.")
        return self._final("```bash: echo e3-ok```")

    def _final(self, content):
        return {
            "id": "chatcmpl-e3", "object": "chat.completion",
            "created": 0, "model": "stub",
            "choices": [{"index": 0,
                         "message": {"role": "assistant", "content": content},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 5,
                      "total_tokens": 10},
        }


def _last_cycle(cd):
    """The most recent cycles.jsonl entry for this company dir."""
    f = cd / "logs" / "cycles.jsonl"
    if not f.exists():
        return None
    lines = [l for l in f.read_text().splitlines() if l.strip()]
    if not lines:
        return None
    return json.loads(lines[-1])


def _mk_company(tmp_path: Path) -> Path:
    cd = tmp_path / ".company"
    (cd / "logs").mkdir(parents=True)
    return cd


def _base_env(tmp_path: Path, cd: Path, mode: str,
              api_base: str = None) -> tuple:
    """Start a stub LLM and build the spawn env. Returns (env, server)."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Responder)
    _Responder.mode = mode
    import threading
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    env = {k: v for k, v in os.environ.items()
           if k not in ("HIVE_ROOT", "HIVE_INSTALL_DIR", "COMPANY_DIR",
                        "HIVE_API_BASE", "HIVE_API_KEY", "HIVE_MODEL")}
    env.update({
        "HIVE_ROOT": str(REPO),
        "HIVE_INSTALL_DIR": str(REPO),
        "COMPANY_DIR": str(cd),
        "HIVE_API_BASE": api_base or f"http://127.0.0.1:{server.server_address[1]}",
        "HIVE_API_KEY": "test",
        "HIVE_MODEL": "stub",
    })
    return env, server


def test_sigterm_is_logged_killed_with_signal(tmp_path):
    """Group TERM (the company-stop.sh path) -> 'killed' + signal TERM.

    Uses a fake harness (pure sleep) so the test is deterministic: the
    spawn-trap contract under test is agent-spawn.sh's, not the LLM
    round-trip. No stream events are ever produced — the 'killed' entry
    must be written even for an empty stream.
    """
    install = tmp_path / "install"
    (install / "lib" / "core").mkdir(parents=True)
    (install / "lib" / "core" / "agent-harness.py").write_text(
        "import time\ntime.sleep(60)\n")
    cd = _mk_company(tmp_path)
    env = {**os.environ,
           "HIVE_ROOT": str(install),
           "HIVE_INSTALL_DIR": str(install),
           "COMPANY_DIR": str(cd)}
    for k in ("HIVE_API_BASE", "HIVE_API_KEY", "HIVE_MODEL", "SKILL_DIR",
              "HIVE_AGENT", "HIVE_DEPT", "HIVE_ROLE", "HIVE_TICKET"):
        env.pop(k, None)
    proc = subprocess.Popen(
        ["bash", str(SPAWN_SH), "--no-mcp", "--", "stand by"],
        cwd=str(tmp_path), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)
    try:
        time.sleep(2.0)
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        proc.wait(timeout=15)
    finally:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        proc.wait(timeout=5)
    entry = _last_cycle(cd)
    assert entry is not None, "no cycles.jsonl entry after a killed run"
    assert entry["status"] == "killed", entry
    assert entry.get("signal") == "TERM", entry


def test_clean_run_is_logged_done(tmp_path):
    """A clean run -> cycles.jsonl carries 'done' (pre-E3 semantics kept)."""
    cd = _mk_company(tmp_path)
    env, server = _base_env(tmp_path, cd, "done")
    try:
        proc = subprocess.run(
            ["bash", str(SPAWN_SH), "--no-mcp", "--", "do a quick task"],
            cwd=str(tmp_path), env=env,
            capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, proc.stderr[-1500:]
    finally:
        server.shutdown()
    entry = _last_cycle(cd)
    assert entry is not None, "no cycles.jsonl entry after a clean run"
    assert entry["status"] == "done", entry
    assert entry["exit_code"] == 0, entry


def test_failing_harness_is_logged_failed(tmp_path):
    """A non-zero harness exit (LLM unreachable) -> 'failed', not 'done'."""
    cd = _mk_company(tmp_path)
    # Port 1 on localhost is closed -> every LLM call fails fast.
    env, server = _base_env(tmp_path, cd, "done",
                            api_base="http://127.0.0.1:1")
    try:
        proc = subprocess.run(
            ["bash", str(SPAWN_SH), "--no-mcp", "--", "anything"],
            cwd=str(tmp_path), env=env,
            capture_output=True, text=True, timeout=120)
    finally:
        server.shutdown()
    # The harness exits non-zero when the LLM is unreachable on turn 0.
    entry = _last_cycle(cd)
    assert entry is not None, "no cycles.jsonl entry after a failed run"
    assert entry["status"] == "failed", entry
    assert entry["exit_code"] != 0, entry
