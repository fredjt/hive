"""Item 5: a company can only touch its OWN git repo, and only when the
operator opted in.

The 2026-11-06 field run: a hive company was hand-scraped into
~/hive/help, a subdirectory of the hive project's own git repo. Nothing
refused it - init/start accepted the nested root, and the first
push/PR would have hit the OUTER project's remote. Now:

  - `hive init` refuses a project root nested in another git repo
    (override: HIVE_ALLOW_NESTED_REPO=1);
  - `hive start` refuses the same, and refuses a .company without the
    init artifacts (no manifest.json);
  - pushes and PRs require the per-company opt-in marker
    (.company/push-enabled, written by `hive gh push enable`) AND the
    company's own repo top level.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _git(args, cwd, env=None):
    e = {**os.environ, "HOME": str(cwd),
         "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
         "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    if env:
        e.update(env)
    r = subprocess.run(["git", *args], cwd=str(cwd), env=e,
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, f"git {args} failed: {r.stderr}"
    return r


def _make_project(base: Path, name="proj") -> Path:
    """A standalone git repo with one commit on main."""
    proj = base / name
    proj.mkdir(parents=True)
    (proj / "README.md").write_text("test\n")
    _git(["init", "-q", "-b", "main"], proj)
    _git(["add", "-A"], proj)
    _git(["commit", "-qm", "seed"], proj)
    return proj


def _run(script: Path, *args, cwd, env=None, timeout=60):
    e = {**os.environ, "HOME": str(cwd), "HIVE_ROOT": str(REPO)}
    if env:
        e.update(env)
    return subprocess.run(["bash", str(script), *args], cwd=str(cwd), env=e,
                          capture_output=True, text=True, timeout=timeout)


class TestInitNestedRepoGuard:
    def test_init_refuses_nested_project_root(self, tmp_path):
        outer = _make_project(tmp_path, "outer")
        nested = outer / "help"
        nested.mkdir()
        r = _run(REPO / "lib" / "core" / "init.sh", str(nested), "--github", "skip",
                 cwd=nested)
        assert r.returncode != 0, f"init should refuse a nested root: {r.stdout}"
        assert "inside another git repository" in r.stderr
        assert not (nested / ".company").exists()

    def test_init_allows_nested_with_explicit_override(self, tmp_path):
        outer = _make_project(tmp_path, "outer")
        nested = outer / "help"
        nested.mkdir()
        r = _run(REPO / "lib" / "core" / "init.sh", str(nested), "--github", "skip",
                 cwd=nested, env={"HIVE_ALLOW_NESTED_REPO": "1"})
        assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
        assert (nested / ".company").exists()

    def test_init_standalone_root_is_unaffected(self, tmp_path):
        proj = _make_project(tmp_path, "solo")
        r = _run(REPO / "lib" / "core" / "init.sh", str(proj), "--github", "skip",
                 cwd=proj)
        assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
        assert (proj / ".company" / "manifest.json").exists()


class TestStartGuards:
    def _company(self, proj: Path, manifest=True):
        company = proj / ".company"
        (company / "logs").mkdir(parents=True)
        if manifest:
            (company / "manifest.json").write_text(
                json.dumps({"project_name": "t", "status": "intake",
                            "mode": "build", "idle_mode": False}))

    def test_start_refuses_nested_root(self, tmp_path):
        outer = _make_project(tmp_path, "outer")
        nested = outer / "help"
        nested.mkdir()
        self._company(nested)
        r = _run(REPO / "lib" / "tools" / "company-start.sh", str(nested),
                 cwd=nested, timeout=30)
        assert r.returncode != 0
        assert "inside another git repository" in r.stderr

    def test_start_refuses_company_without_manifest(self, tmp_path):
        proj = _make_project(tmp_path, "proj")
        self._company(proj, manifest=False)
        r = _run(REPO / "lib" / "tools" / "company-start.sh", str(proj),
                 cwd=proj, timeout=30)
        assert r.returncode != 0
        assert "manifest.json" in r.stderr
        assert "hive init" in r.stderr


def _push_fixture(tmp_path: Path):
    """project + bare 'origin' whose URL contains github.com (so
    gh_remote_ok passes) + a feature branch with a commit."""
    origin = tmp_path / "remotes" / "github.com" / "test" / "proj.git"
    origin.mkdir(parents=True)
    r = subprocess.run(["git", "init", "-q", "--bare", str(origin)],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "README.md").write_text("test\n")
    _git(["init", "-q", "-b", "main"], proj)
    _git(["add", "-A"], proj)
    _git(["commit", "-qm", "seed"], proj)
    _git(["remote", "add", "origin", str(origin)], proj)
    _git(["checkout", "-q", "-b", "feature/T-1"], proj)
    (proj / "feature.txt").write_text("work\n")
    _git(["add", "-A"], proj)
    _git(["commit", "-qm", "feature work"], proj)
    (proj / ".company").mkdir()
    return proj


class TestPushOptIn:
    def test_push_without_optin_is_a_noop(self, tmp_path):
        proj = _push_fixture(tmp_path)
        r = _run(REPO / "lib" / "tools" / "gh-push-branch.sh",
                 "feature/T-1", str(proj), cwd=proj,
                 env={"COMPANY_DIR": str(proj / ".company")})
        assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
        combined = r.stdout + r.stderr
        assert "push not enabled" in combined, combined
        out = subprocess.run(["git", "-C", str(proj), "ls-remote",
                              "--heads", "origin"],
                             capture_output=True, text=True, timeout=30)
        assert "feature/T-1" not in out.stdout, "branch was pushed without opt-in"

    def test_push_with_optin_pushes(self, tmp_path):
        proj = _push_fixture(tmp_path)
        (proj / ".company" / "push-enabled").write_text("opted in\n")
        r = _run(REPO / "lib" / "tools" / "gh-push-branch.sh",
                 "feature/T-1", str(proj), cwd=proj,
                 env={"COMPANY_DIR": str(proj / ".company")})
        assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
        out = subprocess.run(["git", "-C", str(proj), "ls-remote",
                              "--heads", "origin"],
                             capture_output=True, text=True, timeout=30)
        assert "feature/T-1" in out.stdout, "opted-in push did not land"

    def test_push_refused_when_nested(self, tmp_path):
        outer = _make_project(tmp_path, "outer")
        (outer / "remotes") .mkdir(exist_ok=True)
        proj = outer / "help"
        proj.mkdir()
        (proj / "README.md").write_text("help\n")
        _git(["add", "-A"], proj)
        _git(["commit", "-qm", "seed"], proj)
        # the OUTER repo's origin is the github.com-style remote
        origin = outer / "remotes" / "github.com" / "test" / "outer.git"
        origin.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "--bare", str(origin)],
                       check=True, timeout=30)
        _git(["remote", "add", "origin", str(origin)], outer)
        _git(["checkout", "-q", "-b", "feature/T-9"], outer)
        (outer / "f.txt").write_text("x\n")
        _git(["add", "-A"], outer)
        _git(["commit", "-qm", "w"], outer)
        (proj / ".company").mkdir()
        (proj / ".company" / "push-enabled").write_text("opted in\n")
        r = _run(REPO / "lib" / "tools" / "gh-push-branch.sh",
                 "feature/T-9", str(proj), cwd=proj,
                 env={"COMPANY_DIR": str(proj / ".company")})
        assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
        assert "nested in another git repo" in (r.stdout + r.stderr)
        out = subprocess.run(["git", "-C", str(outer), "ls-remote",
                              "--heads", "origin"],
                             capture_output=True, text=True, timeout=30)
        assert "feature/T-9" not in out.stdout, \
            "a nested company pushed to the outer project's remote"


class TestGhPushCli:
    def test_parser_accepts_push_enable_disable(self):
        sys.path.insert(0, str(REPO / "lib"))
        from hive_cli.main import build_parser
        p = build_parser()
        a = p.parse_args(["gh", "push", "enable"])
        assert a.cmd == "gh" and a.action == "push"
        assert a.push_action == "enable"
        b = p.parse_args(["gh", "push", "disable"])
        assert b.push_action == "disable"

    def test_push_enable_writes_marker_disable_removes(self, tmp_path, monkeypatch):
        sys.path.insert(0, str(REPO / "lib"))
        from hive_cli.main import build_parser
        from hive_cli.commands import gh as gh_cmd
        (tmp_path / ".company").mkdir()
        monkeypatch.chdir(tmp_path)
        p = build_parser()
        assert gh_cmd.handle(p.parse_args(["gh", "push", "enable"])) == 0
        assert (tmp_path / ".company" / "push-enabled").exists()
        assert gh_cmd.handle(p.parse_args(["gh", "push", "disable"])) == 0
        assert not (tmp_path / ".company" / "push-enabled").exists()
