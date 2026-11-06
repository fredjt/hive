"""C2: an unparseable near-miss is fed back, never a success.

The live-run failure: the CEO and vp-hr turns each emitted their first
and only turn with the command in a broken tool-call wrapper (space
inside the tags, no fence). parse_actions matched nothing, the loop hit
the no-actions break, and the runs ended is_error: false, subtype: final
with ZERO actions executed.

Now: when a turn carries a near-miss marker (tool_call-style wrappers,
function= lines, or a bash:/mcp: line inside a malformed fence) but
parse_actions yields zero actions, the loop appends a corrective user
message and continues (emitting a parse-warning stream event); a turn
with genuinely no command intent (plain prose) still ends the run; and
the result event carries num_actions so "completed successfully with
zero actions executed" is checkable by any reader of the stream.
"""
import importlib.util
import json
import os
import shlex
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
HARNESS_PATH = REPO / "lib" / "core" / "agent-harness.py"


def _load_harness():
    spec = importlib.util.spec_from_file_location(
        "agent_harness", HARNESS_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


HARNESS = _load_harness()

# The run's exact failure shape: the command in a tool_call-style
# wrapper with a space inside the tags and no fence at all.
BROKEN_WRAPPER = (
    "Let me check the configuration file.\n\n"
    "<tool call>\n"
    "bash: cat /tmp/does-not-matter\n"
    "</tool call>\n"
)


# ── Detector unit tests ──────────────────────────────────────────────

@pytest.mark.parametrize("text, expected", [
    # tool_call-style wrappers — any spelling, spaces inside the tags.
    ("I will run it.\n<tool call>\nbash: ls\n</tool call>", True),
    ("<toolcall>bash: ls</toolcall>", True),
    ("<Tool-Call>\nbash: ls\n</Tool-Call>", True),
    ("< /tool_call >", True),
    # function= lines (broken tool-call serialization).
    ("function=bash(command=\"ls\")", True),
    ("Sure:\nfunction=mcp__hive__ticket_list()", True),
    # a bash:/mcp: line inside an UNCLOSED fence (odd fence count).
    ("Running it:\n```\nbash: ls", True),
    ("```\nmcp: ticket_list()", True),
    # an UNCLOSED standard fence (item 1's form: the ```bash/```mcp
    # language tag after the last marker) — the field run's dropped
    # turns used exactly this shape, malformed.
    ("Running it:\n```bash\ncat /etc/hosts", True),
    ("```mcp\nticket_list(dept=\"hr\")", True),
    # plain prose — genuinely no command intent — must NOT match.
    ("All done with the analysis. No further action needed.", False),
    ("I reviewed the code and found nothing wrong.", False),
    # a well-formed bare fence PARSERSES (C1) — the detector must not
    # flag it (the near-miss branch is only reached on zero actions).
    ("```\nbash: ls\n```", False),
    # a well-formed fenced CODE SAMPLE (even fence count, no keyword).
    ("Example:\n```\nprint(1)\n```\nDone.", False),
])
def test_near_miss_detector(text, expected):
    assert HARNESS._near_miss_command_intent(text) is expected, text


# ── End-to-end: the loop feeds the correction back ───────────────────

def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _final(content):
    return {
        "id": "chatcmpl-c2", "object": "chat.completion",
        "created": int(time.time()), "model": "stub",
        "choices": [{"index": 0,
                     "message": {"role": "assistant", "content": content},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                  "total_tokens": 15},
    }


class _ResolvingLLM(BaseHTTPRequestHandler):
    """mode 'broken': turn 1 emits the run's exact broken wrapper; after
    the harness feeds back the correction, the next turn emits the
    command in the fence-line form; after its Action results, a
    plain-prose final. mode 'prose': every turn is plain prose (no
    command intent at all)."""
    mode = "broken"
    correct_block = ""
    marker_file = None

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        text = "\n".join(
            m.get("content", "") for m in body.get("messages", [])
            if isinstance(m.get("content"), str))
        if self.mode == "prose":
            content = ("All done with the analysis. No further action "
                       "needed.")
        elif "I could not parse that command" in text:
            if "Action results:" in text:
                content = "Done."
            else:
                content = self.correct_block
        else:
            content = BROKEN_WRAPPER
        payload = json.dumps(_final(content)).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *a):  # keep test output quiet
        pass


def _start_stub(handler):
    port = _free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{port}", server


def _run_harness(base_url, tmp_path, timeout=60):
    company = tmp_path / "company"
    company.mkdir(exist_ok=True)
    env = {**os.environ, "HIVE_API_BASE": base_url, "HIVE_MODEL": "stub",
           "COMPANY_DIR": str(company)}
    proc = subprocess.run(
        [sys.executable, str(HARNESS_PATH), "--no-mcp",
         "--api-base", base_url, "--model", "stub", "--max-turns", "10",
         "--", "Check the configuration."],
        env=env, cwd=str(tmp_path), capture_output=True, text=True,
        timeout=timeout)
    lines = [l for l in proc.stdout.strip().splitlines() if l.strip()]
    events = []
    for l in lines:
        try:
            events.append(json.loads(l))
        except json.JSONDecodeError:
            pass
    return proc, events, (lines[-1] if lines else "")


def test_broken_wrapper_is_fed_back_not_success(tmp_path):
    """The run's exact failure: turn 1 in a broken tool-call wrapper.
    The loop must feed the correction (parse-warning event), execute the
    corrected command on the next turn, and the result must carry
    num_actions >= 1 — never a 'successful final answer' with zero
    actions."""
    _ResolvingLLM.mode = "broken"
    marker = tmp_path / "c2-marker.txt"
    _ResolvingLLM.correct_block = (
        "```bash: echo c2-ok > %s```" % shlex.quote(str(marker)))
    _ResolvingLLM.marker_file = marker
    base_url, server = _start_stub(_ResolvingLLM)
    try:
        proc, events, last = _run_harness(base_url, tmp_path)
        assert proc.returncode == 0, proc.stderr

        # The correction was fed back: a parse-warning stream event.
        warnings = [e for e in events if e.get("type") == "parse-warning"]
        assert len(warnings) == 1, (
            f"expected exactly one parse-warning, got {warnings}\n"
            f"stream: {proc.stdout[-800:]}")
        assert "tool call" in warnings[0].get("text", "").lower(), warnings[0]

        # The corrected turn's action EXECUTED.
        assert marker.exists(), "the corrected command never executed"
        assert marker.read_text().strip() == "c2-ok"

        # The result reports how much work the run actually did.
        result = json.loads(last)
        assert result.get("type") == "result"
        assert result.get("is_error") is False
        assert result.get("num_actions", 0) >= 1, result
        # Two counted turns (the corrected command executed; the final
        # prose turn ends the run without incrementing) — never the
        # single uncounted zero-action run the live company produced.
        assert result.get("num_turns", 0) >= 2, result
    finally:
        server.shutdown()


def test_plain_prose_final_has_zero_actions(tmp_path):
    """A turn with genuinely no command intent still ends the run — and
    the result event carries num_actions: 0 (checkable by any reader of
    the stream), with no parse-warning (there was nothing to correct)."""
    _ResolvingLLM.mode = "prose"  # every turn: plain prose, no intent
    base_url, server = _start_stub(_ResolvingLLM)
    try:
        proc, events, last = _run_harness(base_url, tmp_path)
        assert proc.returncode == 0, proc.stderr
        assert not [e for e in events if e.get("type") == "parse-warning"]
        result = json.loads(last)
        assert result.get("is_error") is False
        assert result.get("subtype") == "final"
        assert result.get("num_actions") == 0, result
    finally:
        server.shutdown()
