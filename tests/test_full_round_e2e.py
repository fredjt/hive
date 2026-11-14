"""A1 (0.9.0 flagship): full-round E2E with a stub LLM.

The test that would have caught all five 0.8.x bugs before cut: in a
scratch project, `hive init` -> `hive start` boots the real company
(company-start.sh's real daemons, the real MCP server, the real agent
harness) and drives a one-ticket sprint through a real round against a
local stub LLM. The stub's replies emit the `bash:`/`mcp:` blocks that
advance the ticket — plan -> tdd-red -> tdd-green -> verify ->
build-and-test -> release-verify -> archive — and the test asserts on
terminal state, not on internals:

  * the ticket reaches done (ticket_approve stamps it, the sprint
    archive carries it off with the round),
  * processes.json exists and parses,
  * llm-usage.log has exactly one entry per scripted LLM call,
  * `hive stop` kills every recorded PID.

Design constraints (RELEASE_GOALS): the stub is deterministic — its
responder is a pure function of the prompt text the lifecycle injects
(stage markers + absolute paths), and follow-up turns always carry the
harness's `Action results:` marker; the whole round must finish well
under 2 min so the suite stays xdist-safe (per-test temp dirs,
ephemeral ports). Skipped (not failed) when node/git are missing,
and when the PATH python3 lacks httpx (agent-spawn runs the harness
under it; the harness silently disables LLM/MCP without it)."""
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))  # for stub_llm
from stub_llm import StubLLM  # noqa: E402

VERSION = "0.1.0"
TARBALL_NAME = "scratch-proj-%s.tar.gz" % VERSION

FEATURE_TEST = (
    "def test_add():\n"
    "    from calc import add\n"
    "    assert add(1, 2) == 3\n"
)
FEATURE_IMPL = (
    "def add(a, b):\n"
    "    return a + b\n"
)


class RoundResponder:
    """Scripted LLM responder that advances one full sprint round.

    Every response is a function of the prompt text only (deterministic):
    the lifecycle embeds a `--- STAGE: ... ---` marker plus absolute
    paths (worktree, report) into each agent's prompt, and follow-up
    turns always carry the harness's `Action results:` marker."""

    def __init__(self, repo: Path):
        self.tools = repo / "lib" / "tools"

    @staticmethod
    def _q(value: str) -> str:
        """shlex-quote a path for safe embedding in a generated bash
        command (repo and tmp paths may contain spaces)."""
        return shlex.quote(str(value))

    def _final(self, content: str) -> dict:
        return {
            "id": "chatcmpl-round",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": "stub",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                      "total_tokens": 15},
        }

    @staticmethod
    def _prompt_text(body: dict) -> str:
        parts = []
        for msg in body.get("messages", []):
            c = msg.get("content")
            if isinstance(c, str):
                parts.append(c)
        return "\n".join(parts)

    def respond(self, body: dict) -> dict:
        text = self._prompt_text(body)
        if "Action results:" in text:
            return self._final("Done.")
        if "--- STAGE: ceo-plan ---" in text:
            return self._final(self._ceo_plan())
        m = re.search(r"--- STAGE: vp-plan \(department: (\w+)\) ---", text)
        if m:
            dept = m.group(1)
            if dept == "rd":
                return self._final(self._vp_plan_rd())
            return self._final(f"Nothing to file for {dept} this sprint.")
        m = re.search(r"--- STAGE: tdd-red \(ticket: (\S+?), dept: (\w+)\)", text)
        if m:
            return self._final(self._tdd_red(m.group(1), text))
        m = re.search(r"--- STAGE: tdd-green \(ticket: (\S+?), dept: (\w+)\)", text)
        if m:
            return self._final(self._tdd_green(m.group(1), text))
        m = re.search(r"--- STAGE: verify \(ticket: (\S+?), dept: (\w+)\)", text)
        if m:
            return self._final(self._verify(m.group(1), m.group(2)))
        if "--- STAGE: release-verify ---" in text:
            return self._final(self._release_verify())
        m = re.search(r"--- STAGE: vp-review \(department: (\w+)\) ---", text)
        if m:
            return self._final(self._vp_review(m.group(1), text))
        # Unscripted stage: finish without side effects so the round can
        # never loop on something the stub does not know how to play.
        return self._final("Done. (unscripted stage)")

    def _ceo_plan(self) -> str:
        direction = {
            "rationale": "Prove the full sprint round runs end to end.",
            "departments": {
                "rd": {
                    "direction": "Ship the add() feature end to end.",
                    "seed_tickets": [{
                        "title": "Implement add(a, b)",
                        "type": "feature",
                        "priority": "high",
                        "description": "add(a, b) returns a + b. "
                                       "Acceptance: add(1, 2) == 3.",
                    }],
                },
            },
        }
        cmd = "cat | bash %s <<'JSON'\n%s\nJSON" % (
            self._q(self.tools / "sprint-direction-write.sh"),
            json.dumps(direction))
        return "Writing the sprint direction.\n\n```bash:\n%s\n```" % cmd

    def _vp_plan_rd(self) -> str:
        cmd = (
            'bash %s rd feature high "Implement add(a, b)" '
            '"add(a, b) must return a + b; acceptance: add(1, 2) == 3." '
            '"vp-rd" "sprint-planning"'
            % (self._q(self.tools / "ticket-create.sh")))
        return "Filing the rd ticket.\n\n```bash:\n%s\n```" % cmd

    @staticmethod
    def _worktree(text: str) -> str:
        # The prompt line is `Worktree: <absolute path>`; the path may
        # contain spaces, so capture to end of line rather than one
        # non-space run.
        m = re.search(r"^Worktree: (.+?)\s*$", text, re.MULTILINE)
        assert m, "no worktree path in prompt"
        return m.group(1)

    def _tdd_red(self, tid: str, text: str) -> str:
        wt = self._worktree(text)
        # The root conftest.py makes the worktree root importable from
        # tests/ (pytest puts the conftest's directory on sys.path); the
        # real gates now run for admin depts too (item 7), so the test
        # must genuinely pass, not just be skipped.
        cmd = (
            f"touch {self._q(wt + '/conftest.py')}\n"
            f"mkdir -p {self._q(wt + '/tests')}\n"
            f"cat > {self._q(wt + '/tests/test_add.py')} <<'EOF'\n{FEATURE_TEST}EOF\n"
            f"git -C {self._q(wt)} add conftest.py tests/test_add.py\n"
            f'git -C {self._q(wt)} -c user.name=hive-agent -c user.email=agent@hive.local '
            f'commit -q -m "[TDD-RED] {tid}: failing test for add()"'
        )
        return "Writing the failing test.\n\n```bash:\n%s\n```" % cmd

    def _tdd_green(self, tid: str, text: str) -> str:
        wt = self._worktree(text)
        cmd = (
            f"cat > {self._q(wt + '/calc.py')} <<'EOF'\n{FEATURE_IMPL}EOF\n"
            f"git -C {self._q(wt)} add -A\n"
            f'git -C {self._q(wt)} -c user.name=hive-agent -c user.email=agent@hive.local '
            f'commit -q -m "[TDD-GREEN] {tid}: implement add()"'
        )
        return "Implementing add().\n\n```bash:\n%s\n```" % cmd

    def _verify(self, tid: str, dept: str) -> str:
        return (
            "The implementation is minimal and correct; the [TDD-RED] test "
            "passes.\n\n"
            # rd is an admin dept in the default scaffold (dept-helpers.sh
            # DH_ADMIN_DEPTS), so the lifecycle spawns verification-ic-admin
            # for its tickets.
            "```mcp: ticket_approve(dept=\"%s\", ticket_id=\"%s\", "
            "verifier=\"verification-ic-admin\", rationale=\"add() implemented; "
            "failing test now passes; nothing to simplify.\")```" % (dept, tid))

    def _release_verify(self) -> str:
        cmd = (
            'bash %s GO --rationale "Stub verification: test log green, '
            'version/tarball/sha256 present."'
            % self._q(self.tools / "release-verdict-write.sh"))
        return "Recording the release verdict.\n\n```bash:\n%s\n```" % cmd

    def _vp_review(self, dept: str, text: str) -> str:
        m = re.search(r"sprint review report to:\s*\n\s*(.+?)\s*$", text,
                      re.MULTILINE)
        assert m, "no report path in vp-review prompt"
        report = m.group(1)
        body = (
            f"# {dept} sprint review\n"
            "## What shipped\nadd() feature, proven end to end by the stub round.\n"
            "## What didn't ship\nNothing.\n"
            "## Release outcome\nPublished to dist/releases/ locally.\n"
            "## Signals to leadership\nNone.\n"
        )
        cmd = f"cat > {self._q(report)} <<'EOF'\n{body}EOF"
        return "Writing the sprint review.\n\n```bash:\n%s\n```" % cmd


class TestFullRoundE2E:
    """A1: the suite runs the company, not just the scripts."""

    ROUND_TIMEOUT = 150  # seconds; the round must finish well under 2 min

    # ── helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _free_port() -> int:
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        return port

    @staticmethod
    def _require_prerequisites():
        """Skip (not fail) when node/git are missing — the existing
        optional-skip pattern. Also skip when the PATH python3 lacks
        httpx: with no venv at the repo root, agent-spawn runs the
        harness under PATH python3, and the harness silently disables
        LLM/MCP calls without httpx (the round would never advance)."""
        for tool in ("git", "node"):
            if shutil.which(tool) is None:
                pytest.skip(f"{tool} is not available on this machine")
        py = shutil.which("python3")
        if py is None:
            pytest.skip("python3 is not on PATH")
        r = subprocess.run([py, "-c", "import httpx"], capture_output=True)
        if r.returncode != 0:
            pytest.skip("PATH python3 lacks httpx (the harness LLM/MCP client)")

    @staticmethod
    def _occupy_port(port: int) -> socket.socket:
        """Hold a port with an accept-loop listener so company-start takes
        the 'dashboard already running — reusing it' path and starts no
        node of its own (a test-started dashboard would leak: company-stop
        deliberately does not kill the dashboard)."""
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

    @classmethod
    def _start_mcp_server(cls, company_dir: Path):
        """Boot the REAL MCP server on an ephemeral port (the same command
        `hive start` runs) pointed at the scratch .company. Returns
        (port, stop)."""
        port = cls._free_port()
        env = {
            **os.environ,
            "PYTHONPATH": str(REPO / "lib"),
            "HIVE_ROOT": str(REPO),
            "HIVE_INSTALL_DIR": str(REPO),
            "COMPANY_DIR": str(company_dir),
        }
        log_path = tempfile.mktemp(prefix="hive-a1-mcp-", suffix=".log")
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

    @staticmethod
    def _seed_project(proj: Path):
        """A minimal pytest project: a passing seed test (so the
        build-and-test stage's root-level pytest run has something green),
        a VERSION file (so release-test-suite detects a version), and a
        tiny real tarball in dist/ (so release-publish has an artifact).
        pytest.ini (not pyproject.toml) keeps the lint and security gates
        auto-skipped: only tests-pass is actively evaluated."""
        (proj / "tests").mkdir()
        (proj / "pytest.ini").write_text("[pytest]\ntestpaths = tests\n")
        (proj / "tests" / "test_seed.py").write_text(
            "def test_seed():\n    assert True\n")
        (proj / "VERSION").write_text(VERSION + "\n")
        dist = proj / "dist"
        dist.mkdir()
        payload = dist / "payload.txt"
        payload.write_text(f"{VERSION}: scratch release payload\n")
        with tarfile.open(dist / TARBALL_NAME, "w:gz") as tf:
            tf.add(payload, arcname="payload.txt")
        payload.unlink()

    @staticmethod
    def _init_project(proj: Path, home: Path):
        """`hive init` (the real init.sh) + commit the seeded files so
        worktrees branched from main carry them."""
        env = {
            **os.environ,
            "HOME": str(home),
            "HIVE_ROOT": str(REPO),
            "GIT_AUTHOR_NAME": "hive-agent",
            "GIT_AUTHOR_EMAIL": "agent@hive.local",
            "GIT_COMMITTER_NAME": "hive-agent",
            "GIT_COMMITTER_EMAIL": "agent@hive.local",
        }
        # Make proj its OWN git repo before anything else runs. tmp_path
        # (and any --basetemp) can live INSIDE another git repo — on this
        # machine TMPDIR is <repo>/unsloth-tmp. Without a nested .git,
        # init.sh's `rev-parse --git-dir` sees the enclosing repo, skips
        # `git init`, and every later git op (the seed commit, the
        # round's worktrees and branches) silently targets the enclosing
        # repo — seed commits landed on the real repo's main.
        r = subprocess.run(["git", "init", "-q"], cwd=str(proj), env=env,
                           capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, f"git init failed: {r.stderr}"
        r = subprocess.run(["git", "symbolic-ref", "HEAD", "refs/heads/main"],
                           cwd=str(proj), env=env, capture_output=True,
                           text=True, timeout=60)
        assert r.returncode == 0, f"git symbolic-ref failed: {r.stderr}"

        r = subprocess.run(
            ["bash", str(REPO / "lib" / "core" / "init.sh"), str(proj),
             "--brief", "Scratch project proving the full round runs end to end.",
             "--github", "skip"],
            env=env, capture_output=True, text=True, timeout=120,
        )
        assert r.returncode == 0, f"hive init failed: {r.stdout}\n{r.stderr}"
        for args in (["git", "add", "-A"],
                     ["git", "commit", "-qm",
                      "seed: test scaffolding, version, release tarball"]):
            r = subprocess.run(args, cwd=str(proj), env=env,
                               capture_output=True, text=True, timeout=60)
            assert r.returncode == 0, f"{args} failed: {r.stderr}"

    @staticmethod
    def _run_company(script: str, proj: Path, env: dict, timeout: int):
        """Run a company script with FILE-based output capture (daemons
        inherit the script's stdout; pipe capture would block on EOF until
        every daemon died — the same convention as the other
        company-start tests)."""
        out_path = tempfile.mktemp(prefix="hive-a1-", suffix=".out")
        err_path = tempfile.mktemp(prefix="hive-a1-", suffix=".err")
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

    def _stop_company(self, proj: Path, env: dict):
        """Belt-and-braces stop: company-stop.sh, then SIGKILL anything
        still recorded in processes.json, so a test failure can never
        leave live daemons behind on the dev machine."""
        import signal
        company = proj / ".company"
        procs_file = company / "processes.json"
        try:
            self._run_company("company-stop.sh", proj, env, timeout=90)
        except subprocess.TimeoutExpired:
            pass
        try:
            data = json.loads(procs_file.read_text())
        except Exception:
            return
        for p in data.get("processes", []):
            pid = p.get("pid")
            if not isinstance(pid, int):
                continue
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass  # already dead

    # ── the test ───────────────────────────────────────────────────────

    # T4 (0.22): load-sensitive tail — the heaviest single test in the
    # suite (a FULL company round: ~10 agent spawns, MCP round-trips,
    # the release pipeline). Under parallel load an agent spawn or its
    # LLM round-trip can die in flight (the same signature family as the
    # probe trio's two harness round-trips): the round still completes —
    # but one IC never reached the stub LLM — or the round outlives its
    # wall-clock budget. Both are TRANSIENT (the test passes serially
    # and at low -n), so the test hardens with a bounded single retry
    # INSIDE the test on a FRESH tree per attempt (never a suite re-run):
    # a deterministic failure (a ticket in the wrong state, a missing
    # TDD commit, a dead daemon — any assertion not carrying a transient
    # signature) fails on the first attempt with full pytest granularity.
    ROUND_TRANSIENT_HINTS = (
        "never called the LLM",            # an agent spawn died in flight
        "round did not complete within",   # the wall-clock budget, load
        "hive start failed",               # the company-start chain
    )

    @classmethod
    def _is_round_transient(cls, exc) -> bool:
        msg = str(exc)
        return any(h in msg for h in cls.ROUND_TRANSIENT_HINTS)

    def test_full_round_drives_ticket_to_done(self, tmp_path):
        self._require_prerequisites()

        first = None
        for n in (1, 2):
            try:
                self._full_round_attempt(tmp_path, n)
                return
            except subprocess.TimeoutExpired as e:
                first = first or f"attempt {n}: subprocess timeout: {e}"
            except Exception as e:
                if first is None and not self._is_round_transient(e):
                    raise  # deterministic — full granularity, no retry
                first = first or f"attempt {n}: {str(e)[:300]}"
            if n == 2:
                pytest.fail(f"full round e2e: both attempts failed — {first}")

    def _full_round_attempt(self, tmp_path, n):
        # A FRESH home + proj per attempt: the failed attempt may have
        # left the company half-stopped, and the stub/MCP/dashboard ports
        # are re-allocated below anyway — attempt 2 starts from the same
        # state attempt 1 did, not from its wreckage.
        home = tmp_path / f"home-{n}"
        (home / ".config" / "hive").mkdir(parents=True)
        proj = tmp_path / f"proj-{n}"
        proj.mkdir()
        self._seed_project(proj)
        self._init_project(proj, home)
        company = proj / ".company"
        assert company.is_dir(), "hive init did not create .company"

        mcp_port, stop_mcp = self._start_mcp_server(company)
        stub = StubLLM(RoundResponder(REPO).respond)
        dash_listener = None
        started = False
        env = {}
        try:
            dash_port = self._free_port()
            dash_listener = self._occupy_port(dash_port)
            cfg = tmp_path / "config.toml"
            cfg.write_text(
                "[release]\n"
                "publisher_script = \"%s\"\n"
                % (REPO / "lib" / "templates" / "publishers" / "local-only.sh"))
            env = {
                **os.environ,
                "HOME": str(home),
                "HIVE_ROOT": str(REPO),
                "HIVE_INSTALL_DIR": str(REPO),
                "HIVE_MCP_HOST": "127.0.0.1",
                "HIVE_MCP_PORT": str(mcp_port),
                "HIVE_DASHBOARD_PORT": str(dash_port),
                "HIVE_API_BASE": stub.base_url,
                "HIVE_MODEL": "stub",
                "HIVE_API_KEY": "test-key",
                "HIVE_PROVIDER": "openai",
                "HIVE_START_GRACE": "1",
                "HIVE_CONFIG": str(cfg),
            }

            # hive start
            rc, out, err = self._run_company("company-start.sh", proj, env,
                                             timeout=90)
            assert rc == 0, f"hive start failed:\n{out}\n{err}"
            started = True

            # Drive the round: wait for the lifecycle's completion line.
            logf = company / "logs" / "sprint-lifecycle.log"
            deadline = time.monotonic() + self.ROUND_TIMEOUT
            done = False
            while time.monotonic() < deadline:
                try:
                    txt = logf.read_text(errors="replace")
                except FileNotFoundError:
                    txt = ""
                if "sprint lifecycle complete (new sprint at ceo-plan)" in txt:
                    done = True
                    break
                time.sleep(0.2)
            if not done:
                tail = logf.read_text(errors="replace")[-4000:] if logf.exists() \
                    else "(no lifecycle log)"
                pytest.fail(
                    f"round did not complete within {self.ROUND_TIMEOUT}s; "
                    f"lifecycle log tail:\n{tail}")

            # ── terminal-state assertions ──

            # processes.json exists and parses; every recorded daemon is
            # still alive at round end.
            procs_file = company / "processes.json"
            assert procs_file.exists(), "processes.json missing"
            procs = json.loads(procs_file.read_text())
            recorded = procs["processes"]
            assert len(recorded) >= 7, f"expected the infra daemons: {recorded}"
            for p in recorded:
                for key in ("pid", "role", "department", "interval", "started_at"):
                    assert key in p, f"process entry missing {key}: {p}"
                if p["role"] == "sprint-lifecycle":
                    # Per-sprint driver by design: sprint-lifecycle.sh exits
                    # 0 when the round it drove completes (the completion
                    # line the wait loop above already matched); the 30s
                    # company-health daemon relaunches it for the next
                    # sprint. Its death is the round's success signal.
                    continue
                try:
                    os.kill(p["pid"], 0)
                except ProcessLookupError:
                    pytest.fail(f"daemon {p['role']} PID={p['pid']} died before "
                                f"round completion")

            # llm-usage.log: exactly one entry per scripted LLM call.
            usage_file = company / "logs" / "llm-usage.log"
            assert usage_file.is_file(), "llm-usage.log missing"
            usage = [json.loads(l) for l in usage_file.read_text().splitlines()
                     if l.strip()]
            assert len(usage) == len(stub.requests), (
                f"{len(usage)} usage entries for {len(stub.requests)} "
                f"scripted LLM calls")
            callers = {e["caller"] for e in usage}
            for needed in ("ceo", "vp-rd", "ic-tester", "ic-engineer",
                           "verification-ic-admin", "release-ic"):
                assert needed in callers, (
                    f"{needed} never called the LLM: {sorted(callers)}")

            # The ticket: done, verification-stamped, archived with the round.
            archived = list((company / "departments" / "rd" / "tickets" /
                             "archive").glob("*.json"))
            assert len(archived) == 1, f"expected one archived ticket: {archived}"
            t = json.loads(archived[0].read_text())
            tid = t["id"]
            assert t["status"] == "done", f"ticket not done: {t.get('status')}"
            assert t.get("verified_by"), "ticket_approve did not stamp verified_by"
            assert t.get("archived_in_sprint"), "archive did not stamp the sprint"

            # Both TDD commits exist in the repo (on the branch, or already
            # merged to main by the worktree-merger daemon).
            r = subprocess.run(["git", "-C", str(proj), "log", "--all",
                                "--oneline"], capture_output=True, text=True)
            assert f"[TDD-RED] {tid}" in r.stdout, r.stdout
            assert f"[TDD-GREEN] {tid}" in r.stdout, r.stdout

            # The release ran clean: archived sprint state says GO +
            # published; the local-only publisher laid artifacts down.
            history = list((company / "sprints" / "history").glob("*.json"))
            assert history, "no archived sprint state"
            state = json.loads(max(history, key=lambda p: p.stat().st_mtime)
                               .read_text())
            assert state["stage"] == "complete"
            assert state["release"]["verdict"] == "GO", state["release"]
            assert state["release"]["publish_status"] == "published", \
                state["release"]
            assert (proj / "dist" / "releases" / VERSION /
                    "RELEASE_NOTES.md").exists(), "local publisher left no artifacts"

            # A new sprint started at ceo-plan.
            cur = json.loads((company / "sprints" / "current.json").read_text())
            assert cur["stage"] == "ceo-plan"

            # hive stop kills every recorded PID.
            rc, out, err = self._run_company("company-stop.sh", proj, env,
                                             timeout=90)
            assert rc == 0, f"hive stop failed:\n{out}\n{err}"
            started = False  # stopped cleanly; finally is a no-op
            for p in recorded:
                try:
                    os.kill(p["pid"], 0)
                    pytest.fail(f"daemon {p['role']} PID={p['pid']} survived "
                                f"hive stop")
                except ProcessLookupError:
                    pass  # dead, as required
        finally:
            if started:
                self._stop_company(proj, env)
            if dash_listener is not None:
                dash_listener.close()
            stub.stop()
            stop_mcp()
