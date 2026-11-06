"""D2: the agent shell sees the env the primer promises.

The 0.10.0 field run's CEO spent a turn cat'ing an empty
$HIVE_ROOT/config.toml because $HIVE_ROOT was not exported into the
agent's shell — $HIVE_ROOT/... expanded to /... . agent-spawn.sh now
resolves and exports the full primer section-6 set before exec'ing the
harness; these tests spawn the REAL agent-spawn.sh against a stub LLM
whose first turn runs `env` into a file, then assert the file.
"""
import json
import os
import shlex
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SPAWN = REPO / "lib" / "core" / "agent-spawn.sh"


class _ResolvingLLM(BaseHTTPRequestHandler):
    """Turn 1: a fence-line bash block dumping `env` to ENV_OUT.
    Turn 2 (after the harness's 'Action results:' marker): plain prose
    that ends the run."""
    env_out = None

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n))
        text = " ".join(m.get("content", "") for m in body.get("messages", [])
                        if isinstance(m.get("content"), str))
        if "Action results:" in text:
            content = "Done."
        else:
            content = "```bash: env > " + shlex.quote(str(self.env_out)) + "```"
        payload = {
            "id": "chatcmpl-d2", "object": "chat.completion",
            "created": 1, "model": "stub",
            "choices": [{"index": 0,
                         "message": {"role": "assistant", "content": content},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 5,
                      "total_tokens": 10},
        }
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _spawn_agent(tmp_path, identity, strip=(), home=None):
    """Run the real agent-spawn.sh with a stub LLM in a throwaway project;
    return the env-dump file contents as a dict. `strip` removes vars
    from the caller's env first (guard-case support); `home` overrides
    HOME (the guard case fakes the default install location under it)."""
    server = HTTPServer(("127.0.0.1", 0), _ResolvingLLM)
    env_out = tmp_path / "agent-env.txt"
    _ResolvingLLM.env_out = env_out
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        project = tmp_path / "proj"
        company = project / ".company"
        company.mkdir(parents=True)
        base_env = {k: v for k, v in os.environ.items() if k not in strip}
        base_env.update({
            "HIVE_API_BASE": f"http://127.0.0.1:{server.server_address[1]}",
            "HIVE_API_KEY": "stub",
            "HIVE_MODEL": "stub",
            "HIVE_AGENT": "ic-engineer",
            "HIVE_DEPT": "rd",
            "HIVE_ROLE": "IC",
            "HIVE_TICKET": "T-1",
        })
        if home is not None:
            base_env["HOME"] = str(home)
        if identity:
            base_env.update(identity)
        proc = subprocess.run(
            ["bash", str(SPAWN), "--no-mcp", "--", "dump the env for the test"],
            capture_output=True, text=True, timeout=120,
            cwd=str(project), env=base_env)
        assert proc.returncode == 0, proc.stderr[-2000:]
        assert env_out.exists(), "the agent's `env` action never ran"
        out = {}
        for line in env_out.read_text().splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                out[k] = v
        return out
    finally:
        server.shutdown()


def test_spawned_agent_shell_sees_the_primer_env_set(tmp_path):
    """Every primer section-6 variable spawn can guarantee is present in
    the agent's shell, with the right values. HIVE_ROOT points at the
    install (the repo here), exactly as bin/hive sets it in production."""
    env = _spawn_agent(tmp_path, {"HIVE_ROOT": str(REPO),
                                  "HIVE_INSTALL_DIR": str(REPO)})
    project = tmp_path / "proj"
    for var in ("HIVE_ROOT", "SKILL_DIR", "HIVE_INSTALL_DIR", "PROJECT_ROOT",
                "COMPANY_DIR", "HIVE_AGENT", "HIVE_DEPT", "HIVE_ROLE",
                "HIVE_TICKET"):
        assert var in env, f"{var} missing from the agent shell env"
        assert env[var], f"{var} is empty in the agent shell"
    assert env["HIVE_AGENT"] == "ic-engineer"
    assert env["HIVE_DEPT"] == "rd"
    assert env["HIVE_ROLE"] == "IC"
    assert env["HIVE_TICKET"] == "T-1"
    assert env["PROJECT_ROOT"] == str(project)
    assert env["COMPANY_DIR"] == str(project / ".company")
    assert env["SKILL_DIR"] == env["HIVE_ROOT"]
    # The harness ran FROM this root — the documented path resolves.
    assert os.path.isfile(os.path.join(env["HIVE_ROOT"], "lib", "core",
                                       "agent-harness.py"))


def test_direct_spawn_without_hive_root_still_resolves(tmp_path):
    """Guard case: a caller (direct invocation, no install in the usual
    place) with NO HIVE_ROOT / HIVE_INSTALL_DIR / SKILL_DIR in its env —
    the spawn still resolves and exports the full set. HOME is faked so
    the documented default ($HOME/.local/share/hive) lands on the repo."""
    home = tmp_path / "home"
    (home / ".local" / "share").mkdir(parents=True)
    os.symlink(str(REPO), str(home / ".local" / "share" / "hive"))
    env = _spawn_agent(tmp_path, {},
                       strip=("HIVE_ROOT", "HIVE_INSTALL_DIR", "SKILL_DIR",
                              "PROJECT_ROOT", "COMPANY_DIR"),
                       home=home)
    for var in ("HIVE_ROOT", "SKILL_DIR", "HIVE_INSTALL_DIR", "PROJECT_ROOT",
                "COMPANY_DIR"):
        assert var in env and env[var], f"{var} missing/empty in guard case"
    assert env["PROJECT_ROOT"] == str(tmp_path / "proj")
    assert env["COMPANY_DIR"] == str(tmp_path / "proj" / ".company")
    assert os.path.isfile(os.path.join(env["HIVE_ROOT"], "lib", "core",
                                       "agent-harness.py"))


def test_hive_round_entry_passes_through_when_set(tmp_path):
    """HIVE_ROUND_ENTRY (set by the round runner) reaches the agent shell."""
    env = _spawn_agent(tmp_path, {"HIVE_ROUND_ENTRY": "entry-42",
                                  "HIVE_ROOT": str(REPO)})
    assert env.get("HIVE_ROUND_ENTRY") == "entry-42"
