"""A1: a zero-ticket sprint goes idle instead of cutting empty releases.

A fresh company with no tickets used to fall through the ticket loop
straight into build-and-test -> release-verify -> vp-review, archive a
"complete" sprint with zero shipped work and a failed unversioned release,
and start the next empty sprint — the loop continuing as long as the
company ran (the health supervisor relaunched the one-shot runner every
time). Now: after planning + sprint-fill, with no active tickets and an
empty backlog, the lifecycle enters stage 'idle' (manifest idle_mode=true),
polls for work, and — when a ticket lands — runs sprint-fill and re-enters
the ticket loop. A company that STARTS with a backlog ticket behaves
exactly as before (guard).

Full-company test: real company-start (real daemons incl. the health
supervisor), real MCP server on an ephemeral port, real agent harness
against the local stub LLM (tests/stub_llm.py). HIVE_HEALTH_INTERVAL and
HIVE_IDLE_POLL_INTERVAL are env-shortened so the waits stay seconds, not
minutes. Skipped (not failed) when node/git are missing or the PATH
python3 lacks httpx (agent-spawn runs the harness under it).
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
sys.path.insert(0, str(Path(__file__).resolve().parent))  # for stub_llm
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
    """Hold the dashboard port so company-start takes the 'already running
    — reusing it' path and starts no node of its own (a test-started
    dashboard would leak: company-stop deliberately does not kill it)."""
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
    """Boot the real MCP server on an ephemeral port pointed at the
    scratch .company. Returns (port, stop)."""
    port = _free_port()
    env = {
        **os.environ,
        "PYTHONPATH": str(REPO / "lib"),
        "HIVE_ROOT": str(REPO),
        "HIVE_INSTALL_DIR": str(REPO),
        "COMPANY_DIR": str(company_dir),
    }
    log_path = tempfile.mktemp(prefix="hive-a1mcp-", suffix=".log")
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
    """git init + `hive init` (real init.sh) on an EMPTY project (no test
    framework, no VERSION) — the exact shape of the observed failure."""
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
         "--brief", "Scratch project proving an empty sprint idles.",
         "--github", "skip"],
        env=env, capture_output=True, text=True, timeout=120,
    )
    assert r.returncode == 0, f"hive init failed: {r.stdout}\n{r.stderr}"


def _run_company(script: str, proj: Path, env: dict, timeout: int):
    """Run a company script with file-based output capture (daemons
    inherit the script's stdout; pipe capture would block until every
    daemon died)."""
    out_path = tempfile.mktemp(prefix="hive-a1x-", suffix=".out")
    err_path = tempfile.mktemp(prefix="hive-a1x-", suffix=".err")
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
    """Belt-and-braces stop: company-stop.sh, then SIGKILL anything still
    recorded in processes.json, so a test failure can never leave live
    daemons behind on the dev machine."""
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


def _sprint_state(company: Path) -> dict:
    f = company / "sprints" / "current.json"
    return json.loads(f.read_text()) if f.is_file() else {}


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


class TestIdleSprint:
    """A1: the company stops manufacturing work out of nothing."""

    def _setup_company(self, tmp_path: Path, seed_ticket: bool):
        _require_prerequisites()
        proj = tmp_path / "proj"
        home = tmp_path / "home"
        proj.mkdir(parents=True)
        home.mkdir(parents=True)
        _init_project(proj, home)
        company = proj / ".company"

        if seed_ticket:
            tid = subprocess.run(
                ["bash", str(REPO / "lib" / "tools" / "ticket-create.sh"),
                 "rd", "feature", "high", "Guard ticket",
                 "A backlog ticket present at start — the company must NOT idle.",
                 "test", "manual"],
                capture_output=True, text=True,
                env={"COMPANY_DIR": str(company), **os.environ},
                timeout=30,
            )
            assert tid.returncode == 0, tid.stderr
            assert tid.stdout.strip().splitlines()[-1].startswith("RD-")

        stub = StubLLM()  # default responder: one final "Done." turn
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
            "HIVE_IDLE_POLL_INTERVAL": "2",
            "HIVE_START_GRACE": "1",
            "HIVE_MAX_LOOP_ITERATIONS": "3",
        }
        return proj, company, env, stub, mcp_stop, dash_listener

    def _teardown(self, proj, env, stub, mcp_stop, dash_listener):
        _stop_company(proj, env)
        mcp_stop()
        stub.stop()
        dash_listener.close()

    def test_zero_ticket_company_idles_then_wakes(self, tmp_path):
        proj, company, env, stub, mcp_stop, dash_listener = \
            self._setup_company(tmp_path, seed_ticket=False)
        try:
            rc, out, err = _run_company("company-start.sh", proj, env,
                                             timeout=180)
            assert rc == 0, f"company-start failed:\n{out}\n{err}"

            # ── the company goes idle and STAYS idle ──
            assert _wait_until(
                lambda: _sprint_state(company).get("stage") == "idle", 90
            ), (
                "lifecycle never reached stage idle. sprint state: %s\n"
                "lifecycle log:\n%s"
                % (_sprint_state(company),
                   (company / "logs" / "sprint-lifecycle.log")
                   .read_text(errors="replace")[-2000:])
            )
            manifest = json.loads((company / "manifest.json").read_text())
            assert manifest.get("idle_mode") is True, \
                f"manifest idle_mode not set: {manifest}"

            # No empty release was cut, no empty sprint was archived.
            assert not list((company / "sprints" / "history").glob("*.json")), \
                "an empty sprint was archived"
            rel = company / "releases"
            if rel.is_dir():
                assert not any(rel.iterdir()), "release artifacts exist"

            # The runner is alive after several health cycles (interval 1s)
            # and was never restarted — idling burns no restart budget.
            time.sleep(4)
            procs = json.loads((company / "processes.json").read_text())
            runners = [p for p in procs.get("processes", [])
                       if p.get("role") == "sprint-lifecycle"]
            assert runners, f"no sprint-lifecycle in processes.json: {procs}"
            runner = runners[0]
            assert not runner.get("dead"), f"runner marked dead: {runner}"
            assert runner.get("restart_count", 0) == 0, \
                f"idle runner consumed restart budget: {runner}"
            try:
                os.kill(runner["pid"], 0)
            except OSError:
                pytest.fail("idle runner is not alive")
            health = (company / "logs" / "health.log").read_text()
            restarts = [l for l in health.splitlines()
                        if "sprint-lifecycle" in l and "attempting restart" in l]
            assert not restarts, f"health restarted the idle runner: {restarts}"

            # ── a backlog ticket wakes the runner ──
            r = subprocess.run(
                ["bash", str(REPO / "lib" / "tools" / "ticket-create.sh"),
                 "rd", "feature", "high", "Wake ticket",
                 "File after idle: must promote and start the ticket loop.",
                 "test", "manual"],
                capture_output=True, text=True,
                env={"COMPANY_DIR": str(company), **env}, timeout=30,
            )
            assert r.returncode == 0, r.stderr
            tid = r.stdout.strip().splitlines()[-1]

            def woke():
                st = _sprint_state(company)
                if st.get("stage") == "idle":
                    return False
                ticket_in_backlog = (
                    company / "departments" / "rd" / "tickets" / "backlog"
                    / f"{tid}.json").is_file()
                return not ticket_in_backlog

            assert _wait_until(woke, 45), (
                "idle runner did not wake on backlog work. sprint state: %s"
                % _sprint_state(company)
            )
            manifest = json.loads((company / "manifest.json").read_text())
            assert manifest.get("idle_mode") is not True, \
                "manifest idle_mode still true after wake"
        finally:
            self._teardown(proj, env, stub, mcp_stop, dash_listener)

    def test_backlog_seeded_company_does_not_idle(self, tmp_path):
        """Guard: a company that starts with a backlog ticket behaves
        exactly as before A1 — no idle, the ticket is promoted and works."""
        proj, company, env, stub, mcp_stop, dash_listener = \
            self._setup_company(tmp_path, seed_ticket=True)
        try:
            rc, out, err = _run_company("company-start.sh", proj, env,
                                             timeout=180)
            assert rc == 0, f"company-start failed:\n{out}\n{err}"

            tids = [p.stem for p in
                    (company / "departments" / "rd" / "tickets" / "backlog")
                    .glob("*.json")]
            assert tids, "seed ticket missing"
            tid = tids[0]

            # The ticket must leave the backlog (promoted by sprint-fill
            # and claimed by the ticket loop) WITHOUT the runner ever
            # idling first.
            def processed():
                st = _sprint_state(company)
                if st.get("stage") == "idle":
                    return "idle"
                return "promoted" if not (
                    company / "departments" / "rd" / "tickets" / "backlog"
                    / f"{tid}.json").is_file() else "waiting"

            outcome = _wait_until(
                lambda: processed() == "promoted", 90
            )
            assert outcome, (
                "seed ticket was never promoted; stage=%s"
                % _sprint_state(company).get("stage")
            )
            assert processed() != "idle", "runner idled with work in the backlog"
        finally:
            self._teardown(proj, env, stub, mcp_stop, dash_listener)
