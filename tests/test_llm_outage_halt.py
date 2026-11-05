"""A2: an LLM outage halts the company with escalation, not silent churn.

A live run found the failure class: the LLM endpoint answered 401 for the
first minutes of the company\'s life; every planning spawn failed (the
0.9.0 harness fails fast with subtype llm-unavailable) but the lifecycle
ignored the exit codes, "completed" failed unversioned releases, and
nothing escalated — no incident, no blocker ticket, no state change.

Now: each planning spawn (ceo-plan, vp-plan) or release-verify spawn that
fails with llm-unavailable increments a persisted counter; a successful
planning spawn resets it. At HIVE_LLM_OUTAGE_LIMIT (default 3) the runner
HALTS: an incident in chat/incidents.md names the error, the endpoint and
the sprint; the existing file_release_blocker ticket is filed; a halt
marker lands in .company/rounds/halt; the script exits non-zero. The
health supervisor marks the runner dead for the session (no restart, no
restart-budget burn); 'hive start' (company-start.sh) clears the marker.

Full-company test: real daemons, real MCP on an ephemeral port, real
harness against the stub LLM in FAIL MODE (401), then recovery.
Skipped when node/git are missing or PATH python3 lacks httpx.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from stub_llm import StubLLM  # noqa: E402


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _require_prerequisites():
    for tool in ("git", "node"):
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} is not available on this machine")
    py = shutil.which("python3")
    if py is None:
        pytest.skip("python3 is not on PATH")
    r = subprocess.run([py, "-c", "import httpx"], capture_output=True)
    if r.returncode != 0:
        pytest.skip("PATH python3 lacks httpx (the harness LLM/MCP client)")


def _occupy_port(port: int) -> socket.socket:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", port))
    listener.listen(8)

    def serve():
        while True:
            try:
                conn, _ = listener.accept()
                conn.close()
            except OSError:
                return

    threading.Thread(target=serve, daemon=True).start()
    return listener


def _start_mcp_server(company_dir: Path):
    port = _free_port()
    env = {
        **os.environ,
        "PYTHONPATH": str(REPO / "lib"),
        "HIVE_ROOT": str(REPO),
        "HIVE_INSTALL_DIR": str(REPO),
        "COMPANY_DIR": str(company_dir),
    }
    log_path = tempfile.mktemp(prefix="hive-a2mcp-", suffix=".log")
    log_file = open(log_path, "w")
    proc = subprocess.Popen(
        [sys.executable, str(REPO / "lib" / "hive_mcp" / "server.py"),
         "--transport", "http", "--host", "127.0.0.1", "--port", str(port)],
        env=env, cwd=str(company_dir),
        stdout=log_file, stderr=subprocess.STDOUT,
    )

    def _stop():
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

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            break
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return port, _stop
        except OSError:
            time.sleep(0.2)
    _stop()
    pytest.fail("MCP HTTP server did not open port %d; log:\n%s"
                % (port, Path(log_path).read_text(errors="replace")))


def _init_project(proj: Path, home: Path):
    env = {
        **os.environ,
        "HOME": str(home),
        "HIVE_ROOT": str(REPO),
        "GIT_AUTHOR_NAME": "hive-agent",
        "GIT_AUTHOR_EMAIL": "agent@hive.local",
        "GIT_COMMITTER_NAME": "hive-agent",
        "GIT_COMMITTER_EMAIL": "agent@hive.local",
    }
    (proj / "README.md").write_text("scratch project (empty)\n")
    for args in (["git", "init", "-q"],
                 ["git", "symbolic-ref", "HEAD", "refs/heads/main"],
                 ["git", "add", "-A"],
                 ["git", "commit", "-qm", "seed: empty scratch project"]):
        r = subprocess.run(args, cwd=str(proj), env=env,
                           capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, f"{args} failed: {r.stderr}"
    r = subprocess.run(
        ["bash", str(REPO / "lib" / "core" / "init.sh"), str(proj),
         "--brief", "Scratch project proving an outage halts the runner.",
         "--github", "skip"],
        env=env, capture_output=True, text=True, timeout=120,
    )
    assert r.returncode == 0, f"hive init failed: {r.stdout}\n{r.stderr}"


def _run_company(script: str, proj: Path, env: dict, timeout: int):
    out_path = tempfile.mktemp(prefix="hive-a2x-", suffix=".out")
    err_path = tempfile.mktemp(prefix="hive-a2x-", suffix=".err")
    try:
        with open(out_path, "wb") as out_f, open(err_path, "wb") as err_f:
            proc = subprocess.Popen(
                ["bash", str(REPO / "lib" / "tools" / script), str(proj)],
                stdin=subprocess.DEVNULL, stdout=out_f, stderr=err_f,
                env=env,
            )
            rc = proc.wait(timeout=timeout)
        return rc, Path(out_path).read_text(errors="replace"), \
            Path(err_path).read_text(errors="replace")
    finally:
        for p in (out_path, err_path):
            try:
                os.unlink(p)
            except FileNotFoundError:
                pass


def _stop_company(proj: Path, env: dict):
    company = proj / ".company"
    procs_file = company / "processes.json"
    try:
        _run_company("company-stop.sh", proj, env, timeout=90)
    except Exception:
        pass
    try:
        for p in json.loads(procs_file.read_text()).get("processes", []):
            pid = p.get("pid")
            if pid:
                try:
                    os.kill(pid, 9)
                except OSError:
                    pass
    except Exception:
        pass


def _wait_until(pred, timeout: float, interval: float = 0.5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if pred():
                return True
        except Exception:
            pass
        time.sleep(interval)
    return False


def _runners(company: Path):
    procs_file = company / "processes.json"
    if not procs_file.is_file():
        return []
    procs = json.loads(procs_file.read_text())
    return [p for p in procs.get("processes", [])
            if p.get("role") == "sprint-lifecycle"]


class TestLLMOutageHalt:
    """A2: the company stops hiding its failures."""

    def test_outage_halts_with_escalation_then_recover(self, tmp_path):
        _require_prerequisites()
        proj = tmp_path / "proj"
        home = tmp_path / "home"
        proj.mkdir(parents=True)
        home.mkdir(parents=True)
        _init_project(proj, home)
        company = proj / ".company"

        stub = StubLLM(fail_status=401)  # every LLM call fails
        dash_port = _free_port()
        dash_listener = _occupy_port(dash_port)
        mcp_port, mcp_stop = _start_mcp_server(company)

        env = {
            **os.environ,
            "HOME": str(home),
            "HIVE_ROOT": str(REPO),
            "HIVE_INSTALL_DIR": str(REPO),
            "HIVE_API_BASE": stub.base_url,
            "HIVE_MODEL": "stub",
            "HIVE_MCP_HOST": "127.0.0.1",
            "HIVE_MCP_PORT": str(mcp_port),
            "DASHBOARD_PORT": str(dash_port),
            "HIVE_DASHBOARD_PORT": str(dash_port),
            "HIVE_HEALTH_INTERVAL": "1",
            "HIVE_START_GRACE": "1",
            "HIVE_LLM_OUTAGE_LIMIT": "3",
        }
        try:
            # ── phase 1: the outage halts the runner ──
            rc, out, err = _run_company("company-start.sh", proj, env,
                                        timeout=180)
            # The halt may land inside or after the start grace — either
            # way company-start must not claim a healthy company.
            assert rc in (0, 1), f"company-start rc={rc}:\n{out}\n{err}"

            halt_marker = company / "rounds" / "halt"
            assert _wait_until(halt_marker.is_file, 60), (
                "halt marker never appeared; lifecycle log:\n%s"
                % (company / "logs" / "sprint-lifecycle.log")
                   .read_text(errors="replace")[-2000:]
            )
            marker = json.loads(halt_marker.read_text())
            assert "LLM unavailable" in marker.get("reason", ""), marker
            assert marker.get("endpoint") == stub.base_url, marker
            assert marker.get("sprint_id"), marker
            assert marker.get("failed_spawns", 0) >= 3, marker

            # The incident names the error, the endpoint, the sprint.
            incidents = (company / "chat" / "incidents.md").read_text()
            assert "LLM outage" in incidents
            assert stub.base_url in incidents
            assert marker["sprint_id"] in incidents

            # The blockage is a visible critical bug ticket in the it
            # backlog (the existing file_release_blocker path).
            def blocker_found():
                for p in (company / "departments" / "it" / "tickets"
                          / "backlog").glob("*.json"):
                    t = json.loads(p.read_text())
                    if "RELEASE-BLOCKER" in t.get("title", ""):
                        return t
                return None

            assert _wait_until(blocker_found, 20), \
                f"no RELEASE-BLOCKER ticket in it backlog: {list((company / 'departments' / 'it' / 'tickets' / 'backlog').glob('*.json'))}"
            blocker = blocker_found()
            assert blocker.get("priority") == "critical"
            assert blocker.get("type") == "bug"

            # The health supervisor marks the runner dead for the session
            # with the halt reason — no restart, no restart-budget burn.
            assert _wait_until(
                lambda: any(r.get("dead") and "halted" in
                            (r.get("dead_reason") or "")
                            for r in _runners(company)), 30
            ), f"runner never marked halted-dead: {_runners(company)}"
            runner = [r for r in _runners(company) if r.get("dead")][0]
            assert runner.get("restart_count", 0) == 0, \
                f"halted runner consumed restart budget: {runner}"
            health = (company / "logs" / "health.log").read_text()
            restarts = [l for l in health.splitlines()
                        if "sprint-lifecycle" in l and "attempting restart" in l]
            assert not restarts, f"health restarted the halted runner: {restarts}"

            # No second sprint was created by the spinning loop.
            history = list((company / "sprints" / "history").glob("*.json"))
            assert history == [], f"a sprint was archived during the outage: {history}"

            # ── phase 2: the endpoint recovers; 'hive start' re-arms ──
            stub.fail_status = None  # the stub LLM starts answering now
            rc2, out2, err2 = _run_company("company-start.sh", proj, env,
                                           timeout=180)
            assert rc2 == 0, f"company-start (recovery) failed:\n{out2}\n{err2}"

            # The marker is cleared by hive start itself...
            assert not halt_marker.exists(), "hive start did not clear the halt marker"
            # ...and a successful planning spawn resets the outage counter.
            counter = company / "status" / "llm-outage-count"
            assert _wait_until(
                lambda: (not counter.is_file()) or
                int(counter.read_text().strip() or "0") == 0, 60
            ), f"outage counter never reset: {counter.read_text() if counter.is_file() else None}"

            # The runner is alive again and planning succeeded.
            assert _wait_until(
                lambda: any(not r.get("dead") for r in _runners(company)), 60
            ), f"runner not alive after recovery start: {_runners(company)}"
            assert _wait_until(
                lambda: "CEO plan completed successfully" in
                (company / "logs" / "sprint-lifecycle.log").read_text(
                    errors="replace"), 60
            ), "planning did not succeed after recovery"
        finally:
            _stop_company(proj, env)
            mcp_stop()
            stub.stop()
            dash_listener.close()
