"""C1/C2 (0.9.0): the dashboard runtime guard.

C1 — render-everything smoke: a dependency-free node test (tests/
spa_smoke.js) runs the DELIVERED SPA script (the exact <script> block
the browser receives) under a minimal fake DOM and invokes every
top-level view renderer against two fixture company trees — a healthy
one and a degenerate one (missing files, null fields, one corrupt
JSON) — asserting no exception, non-empty HTML, and no literal
'undefined'/'NaN' in the rendered output.

C2 — one fixture set, two consumers: the canned fetch responses the
smoke replays (tests/fixtures/spa/<tree>/manifest.json) are generated
by running the same runner in record mode against the REAL dashboard
server pointed at the shared fixture tree. A server-side field rename
therefore drifts the live API from the committed fixture (the re-record
comparison below fails) and/or renders literal 'undefined' in the SPA
(the content invariant fails) — the browser is never the first consumer
of a new shape.
"""
import hashlib
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SERVER_JS = REPO / "lib" / "dashboard" / "server.js"
SMOKE = REPO / "tests" / "spa_smoke.js"
FIXTURE_DIR = REPO / "tests" / "fixtures"
TREES = {
    "healthy": FIXTURE_DIR / "company",
    "degenerate": FIXTURE_DIR / "company-degenerate",
}
SPA_FIX_DIR = FIXTURE_DIR / "spa"

# The git views (worktree/diff/branch/commit) hit /api/git/*, which runs
# real `git` in the project root. The fixture trees live inside this
# repo, so their git answers would track THIS repo's commits —
# non-deterministic. Instead both record runs operate on a copy of the
# fixture trees under a scratch project root with a DETERMINISTIC git
# repo (fixed identity, fixed commit dates, fixed contents -> fixed
# hashes), so the recorded git responses are stable across machines.
SCRATCH_ENV_BASE = {
    "GIT_AUTHOR_NAME": "Fixture Author",
    "GIT_AUTHOR_EMAIL": "fixture@example.com",
    "GIT_COMMITTER_NAME": "Fixture Author",
    "GIT_COMMITTER_EMAIL": "fixture@example.com",
}


def _git(args, cwd, date=None):
    env = {**os.environ, **SCRATCH_ENV_BASE}
    if date:
        env["GIT_AUTHOR_DATE"] = date
        env["GIT_COMMITTER_DATE"] = date
    r = subprocess.run(["git"] + list(args), cwd=str(cwd), env=env,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, f"git {' '.join(args)}: {r.stderr}"


def build_scratch_repo(proj: Path):
    """Deterministic scratch repo: 1 commit on main, a worktree on
    feature/DEG-0001 with one commit of its own (so the diff view has
    real changes to render). Same inputs -> same hashes everywhere."""
    _git(["init", "-q", "-b", "main"], proj)
    _git(["config", "user.email", "fixture@example.com"], proj)
    _git(["config", "user.name", "Fixture Author"], proj)
    _git(["config", "commit.gpgsign", "false"], proj)
    (proj / "README.md").write_text("fixture project\n")
    (proj / "app.txt").write_text("v1\n")
    _git(["add", "-A"], proj)
    _git(["commit", "-qm", "fixture: initial commit"], proj,
         date="2026-11-01T09:00:00 +0000")
    wt = proj / ".worktrees" / "DEG-0001"
    _git(["worktree", "add", "-q", str(wt), "-b", "feature/DEG-0001"], proj)
    (wt / "app.txt").write_text("v2\n")
    (wt / "new.txt").write_text("added on the feature branch\n")
    _git(["add", "-A"], wt)
    _git(["commit", "-qm", "fixture: second commit"], wt,
         date="2026-11-02T09:00:00 +0000")


# T3 (0.21) the gh mirror probes the gh the server PROCESS sees: `command
# -v gh` and `gh auth status` run with the server's PATH. Auth state is
# MACHINE-DEPENDENT — the record machine's gh may be logged in, CI
# runners' gh is not — while the committed fixture was recorded with gh
# ON PATH and AUTHENTICATED (gh_authed: true). The record server (the C2
# drift test and gen_spa_fixtures.py) must therefore be given a STUB gh
# that answers `auth status` as authenticated and never reaches the
# network: the scratch project has no origin remote, so
# syncGithubMirror never calls `gh pr/issue list`, and the defensive
# exit 1 below trips if that ever changes. Same pattern as the gh shim
# in tests/test_dashboard.py.
GH_STUB = (
    "#!/bin/sh\n"
    'case "$*" in\n'
    "  'auth status'*) exit 0 ;;\n"
    "  *) echo 'gh stub: network calls are disabled in the fixture run' >&2; exit 1 ;;\n"
    "esac\n"
)

_GH_STUB_DIR = None


def _gh_stub_dir() -> Path:
    """The dir holding the stub `gh` the record server sees (GH_STUB).
    One per process (module scope, hive-* prefix — the reaper reaps it;
    see TestScratchSnowballGuard in tests/test_xdist_contract.py)."""
    global _GH_STUB_DIR
    if _GH_STUB_DIR is None:
        _GH_STUB_DIR = Path(tempfile.mkdtemp(prefix="hive-spa-gh-"))
        stub = _GH_STUB_DIR / "gh"
        stub.write_text(GH_STUB)
        stub.chmod(0o755)
    return _GH_STUB_DIR


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_http(url, timeout=15):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            last = e
            time.sleep(0.15)
    raise AssertionError(f"dashboard never answered {url}: {last}")


# Deterministic mtime for the copied fixture trees (C2). The dashboard serves
# file mtimes verbatim and orders some endpoints by mtime, so a fresh copy or
# a fresh CI checkout would stamp the tree with "now" and every recorded
# manifest would drift on the next run on another machine. Pin every file to
# a fixed base plus a per-path offset: reproducible anywhere, and distinct
# per path so mtime-ordered listings stay stable.
FIXED_MTIME_BASE = 1767225600  # 2026-01-01T00:00:00Z
FIXED_MTIME_SPAN = 86400  # offsets stay within one day of the base


def _pin_tree_times(proj: Path):
    """Pin every file/dir mtime under proj to a deterministic value derived
    from its relative path (a fixed base + path-hash offset)."""
    for p in sorted(proj.rglob("*")):
        if ".git" in p.parts:
            continue
        rel = p.relative_to(proj).as_posix()
        offset = int.from_bytes(
            hashlib.sha256(rel.encode()).digest()[:4], "big") % FIXED_MTIME_SPAN
        ts = FIXED_MTIME_BASE + offset
        os.utime(p, (ts, ts))


class TestSpaSmoke:
    """C1 (render-everything) + C2 (one fixture set, two consumers)."""

    node = shutil.which("node")
    git = shutil.which("git")

    def _needs_node(self):
        if not self.node:
            pytest.skip("node unavailable — cannot run the SPA smoke")

    def _copy_project(self, tmp_path):
        """Copy BOTH fixture trees under one tmp project root with the
        deterministic scratch repo (record mode must never touch the
        repo's fixture trees — /api/departments writes ensureTicketBoard
        skeletons into the company dir). File mtimes are pinned so
        mtime-bearing API fields are stable across machines."""
        proj = tmp_path / "proj"
        proj.mkdir()
        for name in ("healthy", "degenerate"):
            shutil.copytree(TREES[name], proj / ("company" if name == "healthy"
                                                 else "company-degenerate"))
        build_scratch_repo(proj)
        _pin_tree_times(proj)
        return proj

    def _start_server(self, company):
        port = _free_port()
        # The stub gh first on PATH: the host's gh (authed or not) must
        # never decide what /api/gh/mirror reports in a record run.
        env = {**os.environ,
               "PATH": f"{_gh_stub_dir()}{os.pathsep}{os.environ.get('PATH', '')}"}
        proc = subprocess.Popen(
            [self.node, str(SERVER_JS), "--dir", str(company), "--port", str(port)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
        _wait_http(f"http://127.0.0.1:{port}/api/status")
        return proc, port

    def _run_smoke(self, mode, out, report, extra):
        cmd = [self.node, str(SMOKE), "--server", str(SERVER_JS),
               "--mode", mode, "--report", str(report)] + extra
        return subprocess.run(cmd, capture_output=True, text=True, timeout=180,
                              cwd=str(REPO))

    # ── C1: every view renders on both trees ─────────────────────────────
    # (C2 — one fixture set, two consumers — lives in
    #  tests/test_spa_fixture_drift.py: the committed manifests must
    #  match what the live server still produces for these trees.)

    EXPECTED_VIEWS = {
        "dashboard", "sprint", "chats", "roster", "org-chart", "reports",
        "recommendations", "processes", "rounds", "github", "releases",
        "git", "worktree", "worktree-changes", "branch-history",
        "commit-diff", "activity", "global-stream", "verification",
        "brief", "marketing", "config", "connect",
    }

    def _replay(self, name, tmp_path):
        report = tmp_path / f"replay-{name}.json"
        r = self._run_smoke(
            "replay", None, report,
            ["--fixtures", str(SPA_FIX_DIR / name / "manifest.json"),
             "--root-sub", f"<PROJECT_ROOT>={TREES['healthy'].parent}"])
        rep = json.loads(report.read_text()) if report.exists() else {}
        return r, rep

    @pytest.mark.parametrize("name", ["healthy", "degenerate"])
    def test_smoke_renders_all_views(self, name, tmp_path):
        """C1: every top-level view renderer runs to completion against
        the fixture's canned responses — no exception, non-empty HTML,
        and no literal 'undefined'/'NaN' in the rendered output. The
        degenerate tree (empty company parts, null fields, one corrupt
        JSON) is where the 0.8.x field-access class lived: a renderer
        that assumed a shape the data doesn't carry renders 'undefined'
        (or throws) and fails exactly like the browser would have.

        Mutation contract: rename a field a renderer reads (say
        cost_usd -> cost in the rounds table) and this test fails — the
        renamed field renders literal 'undefined' or the access throws.
        """
        self._needs_node()
        r, rep = self._replay(name, tmp_path)
        assert r.returncode == 0, f"smoke {name} failed:\n{r.stdout[-3000:]}"
        views = {v["name"]: v for v in rep.get("views", [])}
        missing = self.EXPECTED_VIEWS - set(views)
        assert not missing, f"views not exercised: {sorted(missing)}"
        bad = {n: v for n, v in views.items()
               if not v["ok"] or v["error"] or v["bad_tokens"]}
        assert not bad, f"views that failed on {name}: {bad}"
        for n, v in views.items():
            assert v["rendered_elements"] > 0 and v["max_rendered_bytes"] >= 10, (
                f"view {n} rendered nothing (name)={name}")
        assert rep.get("misses") == [], (
            f"SPA requested endpoints absent from the fixture (stale "
            f"manifest): {rep['misses']}")
        assert rep.get("unhandled") == [], (
            f"unhandled rejections during the smoke (view threw): "
            f"{rep['unhandled']}")

    # ── record server: the gh state the fixture pins is served, not the
    #    host's (0.21 — CI drifted /api/gh/mirror: the runner's gh is
    #    not authenticated, the record machine's was) ───────────────────

    def test_gh_stub_wins_path_and_reports_authed(self):
        """The stub gh must WIN PATH resolution ahead of any host gh
        (authed or not) and answer `auth status` as authenticated — the
        committed fixture pins gh_authed=true, and that is what the
        record run may depend on."""
        stub_dir = _gh_stub_dir()
        env = {**os.environ,
               "PATH": f"{stub_dir}{os.pathsep}{os.environ.get('PATH', '')}"}
        r = subprocess.run(["sh", "-c", "command -v gh"], env=env,
                           capture_output=True, text=True, timeout=10)
        assert r.returncode == 0 and r.stdout.strip() == str(stub_dir / "gh"), (
            "the stub gh must be the FIRST gh on the record PATH, ahead "
            f"of the host's (got {r.stdout.strip()!r})")
        r = subprocess.run(["sh", "-c", "gh auth status"], env=env,
                           capture_output=True, text=True, timeout=10)
        assert r.returncode == 0, (
            "the stub gh must answer `auth status` as authenticated "
            f"(rc={r.returncode}, {r.stderr.strip()!r})")

    def test_record_server_reports_the_pinned_gh_state(self, tmp_path):
        """The record server (what the C2 drift test and
        gen_spa_fixtures.py run against) must report the PINNED gh
        diagnostics — on PATH, authenticated, no remote — regardless of
        the host's gh: an unauthenticated host gh leaking through would
        make /api/gh/mirror machine-dependent (the 0.21 CI failure)."""
        self._needs_node()
        if not self.git:
            pytest.skip("git unavailable — cannot build the scratch repo")
        proj = self._copy_project(tmp_path)
        server, port = self._start_server(proj / "company")
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/gh/mirror", timeout=5) as r:
                d = json.loads(r.read())
            diag = d["_diagnostics"]
            assert diag["gh_on_path"] is True, (
                "the record server must see a gh on PATH (the stub)")
            assert diag["gh_authed"] is True, (
                "the record server must see an AUTHENTICATED gh — the "
                "committed fixture pins gh_authed=true; a host gh leaking "
                "through would make the fixture machine-dependent")
            assert diag["remote_configured"] is False
            assert d["_sync_error"] == (
                "cannot reach GitHub (check the diagnostic block)")
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
