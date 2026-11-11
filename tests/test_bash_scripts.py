"""Tests for hive bash scripts — invoke and check exit codes/output."""
import sys
import re
import json
import os
import shutil
import subprocess
import time
import tarfile
from pathlib import Path
import hashlib
# Ensure lib/ is on the path when running from within hive/ directory
_hive_lib = str(Path(__file__).resolve().parent.parent / "lib")
if _hive_lib not in sys.path:
    sys.path.insert(0, _hive_lib)



import pytest


SCRIPTS_DIR = "lib/tools"
CORE_DIR = "lib/core"


def run_script(script_path, args=None, env=None, timeout=30):
    """Helper to run a bash script and return the result."""
    cmd = ["bash", str(script_path)]
    if args:
        cmd.extend(args)
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    # Set HIVE_INSTALL_DIR for all script runs
    full_env["HIVE_INSTALL_DIR"] = str(Path(__file__).resolve().parent.parent)

    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, env=full_env
    )
    return result


class TestTicketCreateScript:
    """Test ticket-create.sh."""

    def test_ticket_create_no_args_shows_usage(self):
        result = run_script(f"{SCRIPTS_DIR}/ticket-create.sh")
        assert result.returncode != 0
        assert "Usage" in result.stderr or "usage" in result.stderr.lower()

    def test_ticket_create_bad_priority(self, tmp_company_dir):
        env = {"COMPANY_DIR": str(tmp_company_dir)}
        result = run_script(
            f"{SCRIPTS_DIR}/ticket-create.sh",
            ["it", "feature", "invalid-priority", "Title", "Desc"],
            env=env,
        )
        assert result.returncode != 0

    def test_ticket_create_bad_type(self, tmp_company_dir):
        env = {"COMPANY_DIR": str(tmp_company_dir)}
        result = run_script(
            f"{SCRIPTS_DIR}/ticket-create.sh",
            ["it", "invalid-type", "medium", "Title", "Desc"],
            env=env,
        )
        assert result.returncode != 0

    def test_ticket_create_valid(self, tmp_company_dir):
        """Create a ticket successfully."""
        env = {"COMPANY_DIR": str(tmp_company_dir)}
        result = run_script(
            f"{SCRIPTS_DIR}/ticket-create.sh",
            ["it", "feature", "medium", "Test Ticket", "A test description"],
            env=env,
        )
        assert result.returncode == 0
        # Should output a ticket ID
        assert len(result.stdout.strip()) > 0

    def test_ticket_create_backslash_in_description_stored_verbatim(self, tmp_company_dir):
        """Backslashes in title/description must be stored byte-for-byte.

        Regression: ticket-create.sh interpolated $TITLE/$DESC into a
        python3 -c "'''...'''" literal, so Python interpreted backslash
        escapes (C:\\temp\\new became 'C:<TAB>emp<NEWLINE>ew') — silent data
        corruption for any path/regex in a ticket body.
        """
        env = {"COMPANY_DIR": str(tmp_company_dir)}
        desc = r"Fix the C:\temp\new path and the \t tab"
        title = r"Regex [a-z]+ must survive"
        result = run_script(
            f"{SCRIPTS_DIR}/ticket-create.sh",
            ["it", "bug", "high", title, desc, "test", "qa-finding"],
            env=env,
        )
        assert result.returncode == 0, result.stderr
        ticket_id = result.stdout.strip()
        ticket_file = (tmp_company_dir / "departments" / "it" / "tickets" / "backlog" / f"{ticket_id}.json")
        assert ticket_file.exists(), f"ticket file {ticket_file} was not created"
        data = json.loads(ticket_file.read_text())
        assert data["title"] == title, f"title corrupted: {data['title']!r} != {title!r}"
        assert data["description"] == desc, f"description corrupted: {data['description']!r} != {desc!r}"

    def test_ticket_create_title_with_quotes_creates_matching_ticket(self, tmp_company_dir):
        """Titles containing single quotes (incl. ''') must not crash the
        JSON writer or desync the counter.

        Regression: '''$TITLE''' interpolation produced a SyntaxError for
        quotes; the counter was already incremented, the ticket file was
        never written, and the script printed a phantom ticket ID with
        exit 0.
        """
        env = {"COMPANY_DIR": str(tmp_company_dir)}
        title = "It's done' and '''quoted'''"
        result = run_script(
            f"{SCRIPTS_DIR}/ticket-create.sh",
            ["it", "bug", "high", title, "quote test", "test", "qa-finding"],
            env=env,
        )
        assert result.returncode == 0, result.stderr
        assert "SyntaxError" not in result.stderr
        ticket_id = result.stdout.strip()
        assert ticket_id, "expected a ticket ID on stdout"
        ticket_file = (tmp_company_dir / "departments" / "it" / "tickets" / "backlog" / f"{ticket_id}.json")
        assert ticket_file.exists(), (
            f"phantom ticket ID {ticket_id!r} printed but no ticket file exists"
        )
        data = json.loads(ticket_file.read_text())
        assert data["title"] == title
        # Counter must be consistent with the file we just created.
        counter = json.loads((tmp_company_dir / "departments" / "it" / "tickets" / "counter.json").read_text())
        seq = int(ticket_id.rsplit("-", 1)[1])
        assert counter["next_id"] == seq + 1, (
            f"counter next_id={counter['next_id']} out of sync with ticket {ticket_id}"
        )

    def test_ticket_create_stores_labels_and_acceptance_criteria(self, tmp_company_dir):
        """C.10: the dashboard create form collects labels + acceptance
        criteria, but the write path silently dropped both — every ticket
        came back with labels==[] and acceptance_criteria==[]. The flags
        must round-trip into the ticket JSON, and the flag list must not
        leak into the optional estimate positional (no estimate WARN)."""
        env = {"COMPANY_DIR": str(tmp_company_dir)}
        result = run_script(
            f"{SCRIPTS_DIR}/ticket-create.sh",
            ["it", "feature", "medium", "Labels AC", "desc",
             "tester", "manual",
             "--labels", "auth, api,urgent",
             "--ac", "line one\nline two"],
            env=env,
        )
        assert result.returncode == 0, result.stderr
        assert "WARN" not in result.stderr, (
            f"estimate saw a flag as positional: {result.stderr}"
        )
        files = list((tmp_company_dir / "departments" / "it" / "tickets" / "backlog").glob("*.json"))
        assert len(files) == 1
        data = json.loads(files[0].read_text())
        assert data["labels"] == ["auth", "api", "urgent"]
        assert data["acceptance_criteria"] == ["line one", "line two"]
        assert data["estimate"] == "medium"

    def test_ticket_create_flags_without_estimate_default_medium(self, tmp_company_dir):
        """C.10: with flags and NO positional estimate, the estimate must
        default to medium silently (the 8th positional is the first flag,
        not an estimate)."""
        env = {"COMPANY_DIR": str(tmp_company_dir)}
        result = run_script(
            f"{SCRIPTS_DIR}/ticket-create.sh",
            ["it", "chore", "low", "No est", "d", "t", "manual", "--labels", "x"],
            env=env,
        )
        assert result.returncode == 0, result.stderr
        assert "WARN" not in result.stderr
        files = list((tmp_company_dir / "departments" / "it" / "tickets" / "backlog").glob("*.json"))
        data = json.loads(files[0].read_text())
        assert data["estimate"] == "medium"
        assert data["labels"] == ["x"]


class TestTicketGetScript:
    """Test ticket-get.sh."""

    def test_ticket_get_no_args_shows_usage(self):
        result = run_script(f"{SCRIPTS_DIR}/ticket-get.sh")
        assert result.returncode != 0
        assert "Usage" in result.stderr or "usage" in result.stderr.lower()


class TestTicketClaimScript:
    """Test ticket-claim.sh."""

    def test_ticket_claim_no_args_shows_usage(self):
        result = run_script(f"{SCRIPTS_DIR}/ticket-claim.sh")
        assert result.returncode != 0
        assert "Usage" in result.stderr or "usage" in result.stderr.lower()

    def _create_ticket(self, company_dir, env):
        """Helper: create a ticket and return its ID."""
        result = run_script(
            f"{SCRIPTS_DIR}/ticket-create.sh",
            ["it", "feature", "medium", "Claim me", "A claimable ticket"],
            env=env,
        )
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    def test_ticket_claim_agent_name_with_quotes_does_not_delete_ticket(self, tmp_company_dir):
        """An agent name containing single quotes must not delete the ticket.

        Regression: ticket-claim.sh interpolated '''$AGENT_NAME''' into a
        python3 -c string. A name ending in a quote (or containing ''')
        caused a SyntaxError; the script did not check python's exit
        status, so the unconditional 'rm "$FOUND"' still ran and DELETED
        the ticket — while reporting success.
        """
        env = {"COMPANY_DIR": str(tmp_company_dir)}
        ticket_id = self._create_ticket(tmp_company_dir, env)
        agent_name = "IC-Engineer (it/IT-0001) 'lead'"
        result = run_script(
            f"{SCRIPTS_DIR}/ticket-claim.sh",
            ["it", ticket_id, "IC-Engineer", "ic-it-0001", agent_name],
            env=env,
        )
        assert "SyntaxError" not in result.stderr, result.stderr
        ticket_dir = tmp_company_dir / "departments" / "it" / "tickets"
        claimed = ticket_dir / "in-progress" / f"{ticket_id}.json"
        assert claimed.exists(), (
            f"ticket {ticket_id} missing after claim (deleted? in-progress={claimed.exists()}, "
            f"backlog={(ticket_dir / 'backlog' / f'{ticket_id}.json').exists()})"
        )
        data = json.loads(claimed.read_text())
        assert data["assignee"]["name"] == agent_name
        assert data["status"] == "in-progress"
        assert not (ticket_dir / "backlog" / f"{ticket_id}.json").exists()

    def test_ticket_claim_backslash_in_agent_name_stored_verbatim(self, tmp_company_dir):
        """Backslashes in the agent name must be stored verbatim."""
        env = {"COMPANY_DIR": str(tmp_company_dir)}
        ticket_id = self._create_ticket(tmp_company_dir, env)
        agent_name = r"IC-Engineer C:\work\agent-1"
        result = run_script(
            f"{SCRIPTS_DIR}/ticket-claim.sh",
            ["it", ticket_id, "IC-Engineer", "ic-it-0001", agent_name],
            env=env,
        )
        assert "SyntaxError" not in result.stderr, result.stderr
        claimed = tmp_company_dir / "departments" / "it" / "tickets" / "in-progress" / f"{ticket_id}.json"
        assert claimed.exists(), "ticket missing after claim"
        data = json.loads(claimed.read_text())
        assert data["assignee"]["name"] == agent_name, (
            f"agent name corrupted: {data['assignee']['name']!r} != {agent_name!r}"
        )


class TestReleaseGatesDryRun:
    """release-gates.sh --dry-run must not run the heavy gates or write files.

    Regression: DRY_RUN was parsed from the flag but never referenced, so
    --dry-run still executed the project's full test suite, lint, and
    security audit and still wrote .company/releases/<v>-gates.json.
    """

    def _make_project(self, tmp_path):
        proj = tmp_path / "relproj"
        proj.mkdir()
        (proj / "pytest.ini").write_text("[pytest]\n")
        # conftest is imported when pytest starts: its existence proves the
        # tests gate actually executed the project test suite.
        (proj / "conftest.py").write_text(
            "from pathlib import Path\n"
            "Path(__file__).with_name('tests-ran.marker').write_text('ran')\n"
        )
        subprocess.run(["git", "init", "-q"], cwd=proj, check=True)
        return proj, proj / "tests-ran.marker"

    def test_dry_run_skips_heavy_gates_and_report(self, tmp_path):
        proj, marker = self._make_project(tmp_path)
        result = run_script(
            f"{SCRIPTS_DIR}/release-gates.sh",
            ["--version", "v9.9.9", "--project-root", str(proj), "--dry-run"],
            timeout=120,
        )
        # Cheap read-only gates still ran
        assert "semver" in result.stderr, result.stderr
        assert not marker.exists(), "--dry-run executed the project test suite"
        report = proj / ".company" / "releases" / "v9.9.9-gates.json"
        assert not report.exists(), "--dry-run wrote a report file"

    def test_real_run_executes_tests_and_writes_report(self, tmp_path):
        proj, marker = self._make_project(tmp_path)
        run_script(
            f"{SCRIPTS_DIR}/release-gates.sh",
            ["--version", "v9.9.9", "--project-root", str(proj)],
            timeout=300,
        )
        assert marker.exists(), "tests gate did not run the project test suite"
        report = proj / ".company" / "releases" / "v9.9.9-gates.json"
        assert report.exists(), "no report written on a real run"
        data = json.loads(report.read_text())
        assert data["version"] == "v9.9.9"


class TestQuotedProjectPaths:
    """Scripts must work when the project path contains a single quote.

    Regression: the scripts interpolated paths into python3 -c "..." source
    (open('$FOUND'), glob('$TICKET_DIR/...'), etc.). A path like
    /home/u/O'Brien-proj/.company closed the Python string literal early,
    raising SyntaxError; with stderr discarded (2>/dev/null) every read
    silently returned nothing and every write silently lost data.
    """

    @pytest.fixture()
    def quoted_company_dir(self, tmp_path):
        company = tmp_path / "O'Brien-proj .company"
        company.mkdir()
        (company / "org-chart.json").write_text(json.dumps(
            {"departments": {"it": {"name": "IT"}, "rd": {"name": "R&D"}}}, indent=2))
        (company / "manifest.json").write_text(json.dumps(
            {"project_name": "quoted", "mode": "build"}))
        return company

    def test_ticket_create_list_get_roundtrip(self, quoted_company_dir):
        env = {"COMPANY_DIR": str(quoted_company_dir)}
        r = run_script(
            f"{SCRIPTS_DIR}/ticket-create.sh",
            ["it", "feature", "medium", "Path safety", "desc"], env=env,
        )
        assert r.returncode == 0, r.stderr
        ticket_id = r.stdout.strip()

        r = run_script(f"{SCRIPTS_DIR}/ticket-list.sh", ["it", "--json"], env=env)
        assert r.returncode == 0, r.stderr
        data = json.loads(r.stdout)
        assert data["count"] == 1, f"expected 1 ticket in list, got: {r.stdout[:200]}"
        assert data["tickets"][0]["id"] == ticket_id

        r = run_script(f"{SCRIPTS_DIR}/ticket-get.sh", ["it", ticket_id], env=env)
        assert r.returncode == 0, r.stderr
        assert "Path safety" in r.stdout, (
            f"ticket-get returned nothing for quoted path; stderr: {r.stderr[:200]}"
        )

    def test_chat_read_json(self, quoted_company_dir):
        env = {"COMPANY_DIR": str(quoted_company_dir)}
        chat_dir = quoted_company_dir / "chat"
        chat_dir.mkdir()
        (chat_dir / "general.md").write_text(
            "# Channel: general\n\n"
            "---\n"
            "**[2026-01-01T00:00:00Z] VP-IT (it)**\n"
            "Hello world\n"
        )
        r = run_script(f"{SCRIPTS_DIR}/chat-read.sh", ["general", "--json"], env=env)
        assert r.returncode == 0, r.stderr
        data = json.loads(r.stdout)
        assert data["count"] == 1, f"expected 1 message, got: {r.stdout[:200]}"
        assert data["messages"][0]["message"] == "Hello world"

    def test_sprint_progress_json(self, quoted_company_dir):
        """Regression (goal 8): sprint-progress.sh --json spliced $DEPT_DIR
        into python3 -c source (os.listdir('$DEPT_DIR')). An apostrophe in
        the path raised SyntaxError on stderr, stdout was empty, and the
        dashboard got no data. The JSON must parse and count the ticket."""
        env = {"COMPANY_DIR": str(quoted_company_dir)}
        r = run_script(
            f"{SCRIPTS_DIR}/ticket-create.sh",
            ["it", "feature", "medium", "Progress check", "desc"], env=env,
        )
        assert r.returncode == 0, r.stderr

        r = run_script(f"{SCRIPTS_DIR}/sprint-progress.sh", ["--json"], env=env)
        assert r.returncode == 0, r.stderr
        data = json.loads(r.stdout)
        assert data["totals"]["backlog"] == 1, (
            f"no ticket counted for quoted path; stdout: {r.stdout[:200]}"
        )
        assert data["departments"]["it"]["backlog"] == 1

    def test_ticket_move_in_review_with_assignee(self, quoted_company_dir):
        """Regression (goal 8): ticket-move.sh's in-review assignee check
        spliced $FOUND into python3 -c source (open('$FOUND')). An
        apostrophe in the path raised SyntaxError (stderr discarded via
        2>/dev/null), HAS_ASSIGNEE came back empty, and EVERY in-review
        move was blocked — even tickets with a valid assignee. The check
        must actually read the ticket file."""
        env = {"COMPANY_DIR": str(quoted_company_dir)}
        r = run_script(
            f"{SCRIPTS_DIR}/ticket-create.sh",
            ["it", "feature", "medium", "Review check", "desc"], env=env,
        )
        assert r.returncode == 0, r.stderr
        tid = r.stdout.strip()
        tf = quoted_company_dir / "departments" / "it" / "tickets" / "backlog" / f"{tid}.json"
        data = json.loads(tf.read_text())
        data["assignee"] = {"name": "IC-Engineer", "id": "ic-eng-1"}
        tf.write_text(json.dumps(data, indent=2))

        r = run_script(
            f"{SCRIPTS_DIR}/ticket-move.sh",
            ["it", tid, "in-review", "work done", "IC-Engineer"],
            env={**env, "HIVE_ROLE": "IC"},
        )
        assert r.returncode == 0, (
            f"in-review move was blocked for a quoted path: {r.stderr[:300]}"
        )
        assert (quoted_company_dir / "departments" / "it" / "tickets"
                / "in-review" / f"{tid}.json").exists()

    def test_release_test_suite_tarball_detection_space_in_path(self, tmp_path):
        """Regression (0.9.0 A1): release-test-suite.sh's tarball detection
        ran `find dist/ ... | xargs -r ls -t`. xargs word-splits paths on
        whitespace, so for a project at e.g. /home/u/My Projects/app the
        tarball argument became the bare directory /home/u/My — and ls,
        listing a directory, printed its `dir:` header line, which the
        detection loop stored as .release.tarball ("/home/u/My:").
        release-publish-runner then exited 2 ("tarball not found") and
        every release from a space-path project failed publish."""
        proj = tmp_path / "My Projects" / "app"
        dist = proj / "dist"
        dist.mkdir(parents=True)
        sprints = proj / ".company" / "sprints"
        sprints.mkdir(parents=True)
        (sprints / "current.json").write_text(json.dumps({
            "sprint_id": "sprint-test",
            "stage": "build-and-test",
            "release": {"version": "1.2.3"},
        }, indent=2))
        # NB: not a 0.5.x version string — TestNoStaleVersionStrings
        # forbids 0.5.* outside CHANGELOG.md.
        older = dist / "app-1.2.3.tar.gz"
        newer = dist / "app-1.2.3-rc2.tar.gz"
        older.write_bytes(b"tarball payload")
        newer.write_bytes(b"tarball payload")
        now = time.time()
        os.utime(older, (now - 3600, now - 3600))
        os.utime(newer, (now, now))

        r = run_script(f"{SCRIPTS_DIR}/release-test-suite.sh", [str(proj)],
                       env={"COMPANY_DIR": str(proj / ".company")},
                       timeout=60)
        assert r.returncode == 0, f"exit={r.returncode}\n{r.stdout}\n{r.stderr}"
        state = json.loads((sprints / "current.json").read_text())
        # The most-recently-modified matching tarball, full path intact.
        assert state["release"]["tarball"] == str(newer), state["release"]
        # Sidecar convention: <tarball minus .tar.gz>.sha256 (the same
        # derivation release-publish-runner uses when sha256 is unset).
        sidecar = str(newer)[:-len(".tar.gz")] + ".sha256"
        assert state["release"]["sha256"] == sidecar
        assert os.path.exists(sidecar)

    def test_release_test_suite_version_header_does_not_lie(self, tmp_path):
        """B2: the test-log header printed 'release-test-suite for v$VERSION'
        for EVERY value of VERSION, so the unversioned-<ts> fallback read
        "vunversioned-…" in the run's logs. The v prefix belongs to semver
        values (leading digit) only; the unversioned fallback prints as-is.
        A company WITH a VERSION file (the e2e fixture shape) prints v0.x.y
        exactly as before."""
        def _run_version_case(subdir: str, version_file: str | None,
                              preset_version: str | None = None
                              ) -> tuple[dict, str]:
            proj = tmp_path / subdir
            proj.mkdir(parents=True)
            if version_file is not None:
                (proj / "VERSION").write_text(version_file)
            sprints = proj / ".company" / "sprints"
            sprints.mkdir(parents=True)
            (sprints / "current.json").write_text(json.dumps({
                "sprint_id": "sprint-test",
                "stage": "build-and-test",
                # preset_version: the header is written BEFORE version
                # detection (the log is renamed to <version>-test.log
                # afterwards), so a company that already knows its version
                # — ceo-plan set it, or a prior build-and-test pass did —
                # carries it in the sprint state when the header is
                # written. That is the shape the label assertion needs.
                "release": {"version": preset_version} if preset_version
                           else {},
            }, indent=2))
            r = run_script(f"{SCRIPTS_DIR}/release-test-suite.sh", [str(proj)],
                           env={"COMPANY_DIR": str(proj / ".company")},
                           timeout=60)
            assert r.returncode == 0, (
                f"exit={r.returncode}\n{r.stdout}\n{r.stderr}"
            )
            state = json.loads((sprints / "current.json").read_text())
            log_file = proj / ".company" / "releases" / (
                state["release"]["test_log"][len(str(proj) + "/.company/releases/"):]
            )
            return state["release"], log_file.read_text().splitlines()[0]

        def _label(header: str) -> str:
            """The version label in the header line: '[ts] release-test-suite
            for <LABEL> (project_root=…)' → <LABEL>."""
            return header.split("release-test-suite for ", 1)[1].split(" (", 1)[0]

        # No VERSION file: the unversioned-<ts> fallback prints as-is,
        # without a v prefix.
        rel, header = _run_version_case("no-version", None)
        assert rel.get("version") is None, (
            f"no version should be detected without a VERSION file: {rel}"
        )
        label = _label(header)
        assert label.startswith("unversioned-"), (
            f"the fallback is not in the header as-is: {header}"
        )
        assert not label.startswith("v"), (
            f"the fallback got a v prefix in the header: {header}"
        )

        # A VERSION file (the e2e fixture shape) with the version already in
        # sprint state: v0.x.y as today (a digit-led value keeps the v
        # prefix). NB: not 0.5.x — TestNoStaleVersionStrings forbids it
        # outside CHANGELOG.md.
        rel2, header2 = _run_version_case("with-version", "0.7.4\n",
                                          preset_version="0.7.4")
        assert rel2.get("version") == "0.7.4", rel2
        assert _label(header2) == "v0.7.4", header2


class TestGitWorktreeCreate:
    """git-worktree.sh create must be non-destructive for existing worktrees.

    Regression: the pre-flight 'is this branch already a worktree?' check
    piped `git worktree list --porcelain` through
    `sed -n 's/^worktree //p'`, which strips exactly the `branch` lines it
    then tried to read. Detection always missed, so every create over a
    ticket that already had a worktree fell into the force-rebuild cleanup
    (worktree remove --force + branch -D + fresh branch off main) and
    destroyed the in-progress commits on the ticket's branch — on every
    sprint-lifecycle ensure_worktree re-run (bugfix cycle, daemon restart,
    second pass).
    """

    @pytest.fixture()
    def wt_project(self, tmp_path):
        proj = tmp_path / "wt-proj"
        proj.mkdir()
        gitenv = {**os.environ,
                  "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                  "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}

        def git(*args, cwd=None):
            r = subprocess.run(["git", *args], cwd=str(cwd or proj),
                               capture_output=True, text=True, timeout=30,
                               env=gitenv)
            assert r.returncode == 0, f"git {args} failed: {r.stderr}"
            return r

        git("init", "-b", "main")
        (proj / "README.md").write_text("x\n")
        git("add", "-A")
        git("commit", "-m", "initial")
        wt = proj / ".worktrees" / "W1"
        git("worktree", "add", "-b", "feature/W1", str(wt))
        (wt / "work.md").write_text("in-progress work\n")
        git("add", "-A", cwd=wt)
        git("commit", "-m", "W1: in-progress commit", cwd=wt)
        return proj

    def test_create_reuses_existing_worktree_and_keeps_commits(self, wt_project):
        r = run_script(f"{SCRIPTS_DIR}/git-worktree.sh",
                       ["create", "W1", str(wt_project)])
        assert r.returncode == 0, r.stderr
        assert "Reusing existing worktree" in r.stdout, (
            f"expected the existing worktree to be detected and reused, "
            f"got:\n{r.stdout}\n{r.stderr}"
        )
        # The in-progress commit must survive the idempotent create.
        r2 = subprocess.run(["git", "-C", str(wt_project), "log",
                             "main..feature/W1", "--oneline"],
                            capture_output=True, text=True, timeout=30)
        assert "in-progress commit" in r2.stdout, (
            "create destroyed the branch's commits: "
            f"stdout={r2.stdout!r} stderr={r2.stderr!r}"
        )


class TestSecurityAuditScript:
    """security-audit.sh: audit command + install hint detection (gate 6).

    A missing audit tool is a SKIP, not a pass: the hint output is what
    the gate's skip detail and the dashboard show, so it must name the
    stack and the install command."""

    def test_python_stack_hint_names_bandit(self, tmp_path):
        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / "pyproject.toml").write_text("[project]\nname='x'\n")
        r = run_script(f"{SCRIPTS_DIR}/security-audit.sh",
                       ["cmd", str(wt)])
        assert r.returncode == 0, r.stderr
        cmd = r.stdout.strip()
        if cmd:
            assert "bandit" in cmd or "pip-audit" in cmd, cmd
        h = run_script(f"{SCRIPTS_DIR}/security-audit.sh",
                       ["hint", str(wt)])
        assert h.returncode == 0
        assert "bandit" in h.stdout, h.stdout

    def test_node_stack_always_has_audit_cmd(self, tmp_path):
        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / "package.json").write_text('{"name":"x"}\n')
        r = run_script(f"{SCRIPTS_DIR}/security-audit.sh",
                       ["cmd", str(wt)])
        assert r.returncode == 0
        assert "npm audit" in r.stdout, r.stdout

    def test_rust_stack_hint(self, tmp_path):
        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / "Cargo.toml").write_text('[package]\nname="x"\n')
        h = run_script(f"{SCRIPTS_DIR}/security-audit.sh",
                       ["hint", str(wt)])
        assert "cargo-audit" in h.stdout, h.stdout

    def test_unknown_stack_hint_names_manifests(self, tmp_path):
        wt = tmp_path / "wt"
        wt.mkdir()
        r = run_script(f"{SCRIPTS_DIR}/security-audit.sh",
                       ["hint", str(wt)])
        assert r.returncode == 0
        assert "manifest" in r.stdout, r.stdout

    def test_usage_without_action(self):
        r = run_script(f"{SCRIPTS_DIR}/security-audit.sh", ["bogus", "."])
        assert r.returncode != 0
        assert "Usage" in r.stderr


class TestCompanyStopScript:
    """Test company-stop.sh."""

    def test_company_stop_marks_processes_json_stopped(self, tmp_path):
        """Regression: company-stop.sh interpolated $COMPANY_DIR into
        python3 -c "..." source. With an apostrophe in the project path the
        JSON-update block raised SyntaxError (stderr discarded via
        2>/dev/null), so processes.json was never marked stopped even
        though the script reported success."""
        proj = tmp_path / "O'Co"
        company = proj / ".company"
        (company / "logs").mkdir(parents=True)
        (company / "processes.json").write_text(json.dumps({
            "started_at": "2026-01-01T00:00:00Z",
            "project_root": str(proj),
            "processes": [
                {"pid": 999999, "role": "company-health",
                 "department": "infrastructure", "interval": "30s"},
            ],
        }))
        result = run_script(f"{SCRIPTS_DIR}/company-stop.sh", [str(proj)], timeout=60)
        assert result.returncode == 0, result.stderr
        data = json.loads((company / "processes.json").read_text())
        assert "stopped_at" in data, "processes.json was not marked stopped"
        assert data["processes"] == []


class TestTicketListScript:
    """Test ticket-list.sh."""

    def test_ticket_list_no_args_is_company_wide(self, tmp_company_dir):
        """T2 (0.17): the department argument is optional — no args is
        the company-wide view (the 0.16 usage error is gone by design;
        an empty company names itself instead of staying silent)."""
        env = {"COMPANY_DIR": str(tmp_company_dir)}
        result = run_script(f"{SCRIPTS_DIR}/ticket-list.sh", env=env)
        assert result.returncode == 0, result.stderr
        assert "No departments found" in result.stdout




class TestTicketListCompanyWide:
    """T2 (0.17): ticket-list.sh without a department lists the whole
    company, grouped; a named missing department is an error distinct
    from an existing-but-empty one (count 0, exit 0)."""

    def _seed(self, company):
        for dept, tid, pri in [("rd", "RD-0101", "high"),
                               ("rd", "RD-0102", "low"),
                               ("it", "IT-0101", "medium")]:
            d = company / "departments" / dept / "tickets" / "backlog"
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{tid}.json").write_text(json.dumps(
                {"id": tid, "title": tid, "status": "backlog",
                 "type": "feature", "priority": pri}))
        empty = company / "departments" / "hr" / "tickets" / "backlog"
        empty.mkdir(parents=True, exist_ok=True)

    def test_company_wide_json_groups_by_department(self, tmp_company_dir):
        self._seed(tmp_company_dir)
        r = run_script(f"{SCRIPTS_DIR}/ticket-list.sh", ["--json"],
                       env={"COMPANY_DIR": str(tmp_company_dir)})
        assert r.returncode == 0, r.stderr
        d = json.loads(r.stdout)
        assert set(d.keys()) == {"departments", "total", "mode"}
        assert d["total"] == 3  # rd 2 + it 1 + hr 0
        assert set(d["departments"].keys()) == {"rd", "it", "hr"}
        assert d["departments"]["rd"]["count"] == 2
        assert d["departments"]["it"]["count"] == 1
        # The per-dept docs keep the 0.16 schema
        rd = d["departments"]["rd"]
        assert rd["department"] == "rd" and rd["mode"] == "summary"
        assert set(rd["tickets"][0].keys()) == {
            "id", "title", "status", "type", "priority", "assignee",
            "gates_passed", "verified_by", "skip_tdd_red",
            "comment_count", "last_comment"}

    def test_per_dept_json_unchanged(self, tmp_company_dir):
        self._seed(tmp_company_dir)
        r = run_script(f"{SCRIPTS_DIR}/ticket-list.sh", ["rd", "--json"],
                       env={"COMPANY_DIR": str(tmp_company_dir)})
        assert r.returncode == 0, r.stderr
        d = json.loads(r.stdout)
        assert set(d.keys()) == {"department", "count", "tickets", "mode"}
        assert d["count"] == 2

    def test_missing_department_is_an_error(self, tmp_company_dir):
        self._seed(tmp_company_dir)
        r = run_script(f"{SCRIPTS_DIR}/ticket-list.sh", ["nope", "--json"],
                       env={"COMPANY_DIR": str(tmp_company_dir)})
        assert r.returncode == 1
        assert "Department 'nope' not found" in r.stderr

    def test_existing_empty_department_is_count_zero(self, tmp_company_dir):
        self._seed(tmp_company_dir)
        r = run_script(f"{SCRIPTS_DIR}/ticket-list.sh", ["hr", "--json"],
                       env={"COMPANY_DIR": str(tmp_company_dir)})
        assert r.returncode == 0, r.stderr
        d = json.loads(r.stdout)
        assert d["count"] == 0 and d["tickets"] == []

    def test_company_wide_priority_filter(self, tmp_company_dir):
        self._seed(tmp_company_dir)
        r = run_script(f"{SCRIPTS_DIR}/ticket-list.sh",
                       ["--priority", "high", "--json"],
                       env={"COMPANY_DIR": str(tmp_company_dir)})
        assert r.returncode == 0, r.stderr
        d = json.loads(r.stdout)
        assert d["total"] == 1
        assert d["departments"]["rd"]["count"] == 1
        assert d["departments"]["it"]["count"] == 0


class TestTicketMoveScript:
    """Test ticket-move.sh."""

    def test_ticket_move_no_args_shows_usage(self):
        result = run_script(f"{SCRIPTS_DIR}/ticket-move.sh")
        assert result.returncode != 0
        assert "Usage" in result.stderr or "usage" in result.stderr.lower()

    def test_ticket_move_done_requires_reason(self, tmp_company_dir):
        env = {"COMPANY_DIR": str(tmp_company_dir)}
        # Try to move to done without a reason
        result = run_script(
            f"{SCRIPTS_DIR}/ticket-move.sh",
            ["it", "TICKET-001", "done", "comment"],
            env=env,
        )
        assert result.returncode != 0
        assert "reason" in result.stderr.lower() or "ERROR" in result.stderr

    def test_ticket_move_done_clears_skip_tdd_red(self, tmp_company_dir):
        """Regression (release goal A.4): a ticket that reached done
        with skip_tdd_red=true kept the flag forever (ticket-move did not
        touch it), so a ticket re-promoted in a future sprint skipped the
        RED stage again. ticket-move clears it when moving to done."""
        env = {"COMPANY_DIR": str(tmp_company_dir)}
        r = run_script(f"{SCRIPTS_DIR}/ticket-create.sh",
                       ["it", "feature", "medium", "A4 flag", "desc"], env=env)
        assert r.returncode == 0, r.stderr
        tid = r.stdout.strip()
        tf = tmp_company_dir / "departments" / "it" / "tickets" / "backlog" / f"{tid}.json"
        data = json.loads(tf.read_text())
        data["skip_tdd_red"] = True
        tf.write_text(json.dumps(data, indent=2))

        r = run_script(f"{SCRIPTS_DIR}/ticket-move.sh",
                       ["it", tid, "done", "shipped", "tester", "implemented"],
                       env={**env, "HIVE_SCRIPTED_MOVE": "1"})
        assert r.returncode == 0, r.stderr
        df = tmp_company_dir / "departments" / "it" / "tickets" / "done" / f"{tid}.json"
        data = json.loads(df.read_text())
        assert "skip_tdd_red" not in data, (
            "skip_tdd_red survived the done move"
        )
        assert data["resolution"]["status"] == "implemented"


# python3 -c with a DOUBLE-quoted program: a literal quote, then any run of
# (non-quote, non-backslash char) or (backslash + any char), then the
# closing quote. A $ anywhere inside means bash interpolated into python
# source — the bug class this guard closes. Single-quoted programs and
# env-var + quoted-heredoc forms do not match.
_PYC_INTERP_RE = re.compile(r'python3?\s+-c\s+"((?:[^"\\]|\\.)*)"')


def _find_python_c_interpolation(text):
    """Return [(line, code)] for every double-quoted python3 -c program in
    shell source that contains a $ — i.e. a bash variable or
    command substitution spliced into python source. Single-quoted programs
    (no interpolation happens) and env-var + quoted-heredoc patterns are
    safe and do not match."""
    return [
        (text.count("\n", 0, m.start()) + 1, m.group(1))
        for m in _PYC_INTERP_RE.finditer(text)
        if "$" in m.group(1)
    ]


class TestNoPythonCInterpolation:
    """Goal 8 lint guard: keep the double-quoted python3 -c interpolation
    class closed for good.

    A path or title containing an apostrophe breaks the quoting of a
    double-quoted -c program (SyntaxError), and the usual `2>/dev/null`
    makes the failure silent — empty reads, lost writes, phantom success.
    The safe pattern is env vars + a quoted heredoc:

        FOUND="$FOUND" python3 - <<'PYEOF'
        import json, os
        t = json.load(open(os.environ['FOUND']))
        PYEOF
    """

    SH_DIRS = ("bin", "lib", "packaging")

    @property
    def _root(self):
        return Path(__file__).resolve().parent.parent

    def _shell_scripts(self):
        root = self._root
        files = []
        for d in self.SH_DIRS:
            base = root / d
            if base.is_dir():
                files.extend(sorted(base.rglob("*.sh")))
        files.extend(sorted(root.glob("*.sh")))
        hive = root / "bin" / "hive"
        if hive.is_file():
            files.append(hive)
        return files

    def test_no_bash_vars_in_double_quoted_python_c(self):
        offenders = []
        for sh in self._shell_scripts():
            for line, code in _find_python_c_interpolation(sh.read_text()):
                rel = sh.relative_to(self._root)
                first = code.splitlines()[0][:60] if code.splitlines() else ""
                offenders.append(f"{rel}:{line}: {first!r}")
        assert not offenders, (
            "python3 -c with bash interpolation (pass vars via environment "
            "and use a quoted heredoc instead):\n  " + "\n  ".join(offenders)
        )

    def test_guard_detects_the_interpolation_pattern(self):
        """The lint must actually see the pattern it guards against —
        multi-line double-quoted programs included."""
        sample = 'X=$(python3 -c "\nimport os\nprint(os.listdir(\'\'\'$D\'\'\'))\n")'
        hits = _find_python_c_interpolation(sample)
        assert len(hits) == 1, hits
        assert "$D" in hits[0][1]
        # Safe forms must NOT be flagged.
        assert _find_python_c_interpolation(
            "python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))'"
        ) == []
        assert _find_python_c_interpolation(
            'FOUND="$FOUND" python3 - <<\'PYEOF\'\n'
            "import os\nopen(os.environ['FOUND'])\nPYEOF"
        ) == []


class TestTicketCommentScript:
    """Test ticket-comment.sh."""

    def test_ticket_comment_no_args_shows_usage(self):
        result = run_script(f"{SCRIPTS_DIR}/ticket-comment.sh")
        assert result.returncode != 0
        assert "Usage" in result.stderr or "usage" in result.stderr.lower()


class TestChatSendScript:
    """Test chat-send.sh."""

    def test_chat_send_no_args_shows_usage(self):
        result = run_script(f"{SCRIPTS_DIR}/chat-send.sh")
        assert result.returncode != 0
        assert "Usage" in result.stderr or "usage" in result.stderr.lower()


class TestChatReadScript:
    """Test chat-read.sh."""

    def test_chat_read_no_args_shows_usage(self):
        result = run_script(f"{SCRIPTS_DIR}/chat-read.sh")
        assert result.returncode != 0
        assert "Usage" in result.stderr or "usage" in result.stderr.lower()


class TestInitWorkspaceScript:
    """Test init-workspace.sh."""

    def test_init_workspace_no_args_shows_usage(self):
        result = run_script(f"{SCRIPTS_DIR}/init-workspace.sh")
        assert result.returncode != 0
        assert "Usage" in result.stderr or "usage" in result.stderr.lower()


class TestInitManifestScript:
    """Test init-manifest.sh."""

    def test_init_manifest_no_args_shows_usage(self):
        result = run_script(f"{SCRIPTS_DIR}/init-manifest.sh")
        assert result.returncode != 0
        assert "Usage" in result.stderr or "usage" in result.stderr.lower()


class TestInitBriefScript:
    """Test init-brief.sh."""

    def test_init_brief_no_args_shows_usage(self):
        result = run_script(f"{SCRIPTS_DIR}/init-brief.sh")
        assert result.returncode != 0
        assert "Usage" in result.stderr or "usage" in result.stderr.lower()


class TestInitChatChannelsScript:
    """Test init-chat-channels.sh."""

    def test_init_chat_channels_no_args_works(self):
        """Should work even with no args (uses defaults)."""
        result = run_script(f"{SCRIPTS_DIR}/init-chat-channels.sh")
        # May or may not fail depending on implementation


class TestValidateWorkspaceScript:
    """Test validate-workspace.sh."""

    def test_validate_workspace_no_args_shows_usage(self):
        result = run_script(f"{SCRIPTS_DIR}/validate-workspace.sh")
        assert result.returncode != 0
        assert "Usage" in result.stderr or "usage" in result.stderr.lower()


class TestCheckCompanyDirScript:
    """Test check-company-dir.sh."""

    def test_check_company_dir_no_args_shows_usage(self):
        result = run_script(f"{SCRIPTS_DIR}/check-company-dir.sh")
        assert result.returncode != 0
        assert "Usage" in result.stderr or "usage" in result.stderr.lower()


class TestStartupBanners:
    """Items 10/11: the start-time warnings and ports tell the truth."""

    def test_rounds_absence_does_not_warn(self, tmp_path):
        """Item 10: rounds/ is created at RUNTIME by the runner, after the
        check - a freshly init'd company (no rounds yet) must not warn
        'missing expected subdirs: rounds' on every start."""
        proj = tmp_path / "proj"
        company = proj / ".company"
        for sub in ("departments", "sprints", "logs"):
            (company / sub).mkdir(parents=True)
        result = subprocess.run(
            ["bash", f"{SCRIPTS_DIR}/check-company-dir.sh", str(proj)],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0, result.stderr
        assert "missing expected subdirs" not in result.stderr, (
            f"fresh company warned about runtime dirs: {result.stderr}"
        )

    def test_banner_uses_dashboard_port_not_8080(self):
        """Item 11: nothing listens on 8080; the board is on
        $DASHBOARD_PORT (default 3456). The banner must not print a
        port that has no listener."""
        src = (Path(SCRIPTS_DIR) / "company-start.sh").read_text()
        assert "localhost:8080" not in src, (
            "start banner still hardcodes port 8080"
        )
        assert 'Management Dashboard: http://localhost:$DASHBOARD_PORT' in src


class TestSprintStateScript:
    """Test sprint-state.sh — sourcing and functions."""

    def test_sprint_state_has_valid_stages(self):
        """sprint-state.sh should define SS_VALID_STAGES."""
        # Source the script and check for the variable
        code = f"""
source "{SCRIPTS_DIR}/sprint-state.sh" 2>/dev/null
echo "STAGES=${{SS_VALID_STAGES[@]}}"
"""
        result = subprocess.run(
            ["bash", "-c", code],
            capture_output=True, text=True, timeout=10,
            env={**os.environ, "HIVE_INSTALL_DIR": str(Path(__file__).resolve().parent.parent)},
        )
        assert result.returncode == 0
        # Should contain stage names
        assert "ceo-plan" in result.stdout


class TestStatusRebuildScript:
    """status-rebuild.sh daemon contract: quiet mode must exit 0 on success.

    2026-11-06 field run: the company's status-rebuild.log held one
    'exited code=1, restarting in 5s' line PER MINUTE for the whole session —
    a perpetual crash loop. The script succeeded every time; its final line
    was `[ "$QUIET" = false ] && echo ...`, which returns 1 when QUIET=true,
    so the daemon wrapper (company-start.sh) saw a crash each cycle.
    """

    def _mk_company(self, tmp_path):
        company = tmp_path / ".company"
        (company / "departments" / "hr").mkdir(parents=True)
        return company

    def test_quiet_mode_exits_zero_on_success(self, tmp_path):
        company = self._mk_company(tmp_path)
        result = subprocess.run(
            ["bash", f"{SCRIPTS_DIR}/status-rebuild.sh", "--quiet"],
            capture_output=True, text=True, timeout=30,
            env={**os.environ, "COMPANY_DIR": str(company)},
        )
        assert result.returncode == 0, (
            f"status-rebuild --quiet exited {result.returncode} on a "
            f"successful run (daemon would crash-loop): {result.stderr}")
        status_file = company / "departments" / "hr" / "status.json"
        assert status_file.exists(), "status.json was not written"

    def test_loud_mode_still_reports(self, tmp_path):
        company = self._mk_company(tmp_path)
        result = subprocess.run(
            ["bash", f"{SCRIPTS_DIR}/status-rebuild.sh"],
            capture_output=True, text=True, timeout=30,
            env={**os.environ, "COMPANY_DIR": str(company)},
        )
        assert result.returncode == 0
        assert "Status rebuilt for 1 departments" in result.stderr


class TestDeptHelpersScript:
    """Test dept-helpers.sh."""

    def test_dept_helpers_has_is_admin_dept(self):
        """dept-helpers.sh should define is_admin_dept function."""
        code = f"""
source "{SCRIPTS_DIR}/dept-helpers.sh" 2>/dev/null
type is_admin_dept >/dev/null 2>&1 && echo "FOUND" || echo "NOT_FOUND"
"""
        result = subprocess.run(
            ["bash", "-c", code],
            capture_output=True, text=True, timeout=10,
            env={**os.environ, "HIVE_INSTALL_DIR": str(Path(__file__).resolve().parent.parent)},
        )
        assert result.returncode == 0


class TestTicketPathsScript:
    """Test ticket-paths.sh."""

    def test_ticket_paths_has_ticket_dir_function(self):
        """ticket-paths.sh should define ticket_dir function."""
        code = f"""
source "{SCRIPTS_DIR}/ticket-paths.sh" 2>/dev/null
type ticket_dir >/dev/null 2>&1 && echo "FOUND" || echo "NOT_FOUND"
"""
        result = subprocess.run(
            ["bash", "-c", code],
            capture_output=True, text=True, timeout=10,
            env={**os.environ, "HIVE_INSTALL_DIR": str(Path(__file__).resolve().parent.parent)},
        )
        assert result.returncode == 0


class TestGhHelpersScript:
    """Test gh-helpers.sh."""

    def test_gh_helpers_has_functions(self):
        """gh-helpers.sh should define helper functions."""
        code = f"""
source "{SCRIPTS_DIR}/gh-helpers.sh" 2>/dev/null
echo "AUTH_OK=$(type gh_auth_ok >/dev/null 2>&1 && echo FOUND || echo NOT_FOUND)"
echo "REMOTE_OK=$(type gh_remote_ok >/dev/null 2>&1 && echo FOUND || echo NOT_FOUND)"
"""
        result = subprocess.run(
            ["bash", "-c", code],
            capture_output=True, text=True, timeout=10,
            env={**os.environ, "HIVE_INSTALL_DIR": str(Path(__file__).resolve().parent.parent)},
        )
        assert result.returncode == 0


class TestCoreScripts:
    """Test core scripts (bin/hive, init.sh, orchestrator.sh, agent-spawn.sh)."""

    def test_bin_hive_exists(self):
        assert Path("bin/hive").exists()

    def test_bin_hive_is_executable(self):
        assert os.access("bin/hive", os.X_OK)

    def test_core_init_sh_exists(self):
        assert Path(f"{CORE_DIR}/init.sh").exists()

    def test_core_orchestrator_sh_exists(self):
        assert Path(f"{CORE_DIR}/orchestrator.sh").exists()

    def test_core_agent_spawn_sh_exists(self):
        assert Path(f"{CORE_DIR}/agent-spawn.sh").exists()


class TestAgentSpawnVerificationModel:
    """B.1: verification agents pull their model from [llm].verification_model.

    Precedence: HIVE_VERIFICATION_MODEL env > user config > install
    defaults. The harness is stubbed (it reports its argv) so the test
    exercises the real agent-spawn.sh logic without an LLM call.
    """

    def _scratch(self, tmp_path):
        root = tmp_path / "hive-root"
        (root / "lib" / "core").mkdir(parents=True)
        (root / "etc").mkdir()
        (root / "lib" / "core" / "agent-harness.py").write_text(
            "import sys, json\n"
            "print(json.dumps({'argv': sys.argv[1:]}))\n")
        (root / "etc" / "config.defaults.toml").write_text(
            '[llm]\nverification_model = "default-vm"\n')
        home = tmp_path / "home"
        (home / ".config" / "hive").mkdir(parents=True)
        return root, home

    def _run_spawn(self, tmp_path, root, home, role, extra=None):
        env = os.environ.copy()
        env.pop("HIVE_VERIFICATION_MODEL", None)
        env.update({
            "HIVE_ROOT": str(root),
            "HOME": str(home),
            "HIVE_ROLE": role,
            "COMPANY_DIR": str(tmp_path / "company"),
            # Do not source the real operator env file.
            "HIVE_ENV_FILE": str(tmp_path / "no-such-env.sh"),
        })
        if extra:
            env.update(extra)
        r = subprocess.run(
            ["bash", f"{CORE_DIR}/agent-spawn.sh", "--", "test prompt"],
            capture_output=True, text=True, timeout=30, env=env)
        assert r.returncode == 0, f"{r.stdout!r} {r.stderr!r}"
        for line in r.stdout.splitlines():
            if line.startswith("{"):
                return json.loads(line)
        raise AssertionError(f"no harness argv line in output: {r.stdout!r}")

    @staticmethod
    def _model_from(argv):
        return argv[argv.index("--model") + 1] if "--model" in argv else None

    def test_verification_model_from_install_defaults(self, tmp_path):
        root, home = self._scratch(tmp_path)
        argv = self._run_spawn(tmp_path, root, home, "verification-IC")
        assert self._model_from(argv["argv"]) == "default-vm"

    def test_user_config_beats_install_defaults(self, tmp_path):
        root, home = self._scratch(tmp_path)
        (home / ".config" / "hive" / "config.toml").write_text(
            '[llm]\nverification_model = "user-vm"\n')
        argv = self._run_spawn(tmp_path, root, home, "verification-IC")
        assert self._model_from(argv["argv"]) == "user-vm"

    def test_env_var_beats_user_config(self, tmp_path):
        root, home = self._scratch(tmp_path)
        (home / ".config" / "hive" / "config.toml").write_text(
            '[llm]\nverification_model = "user-vm"\n')
        argv = self._run_spawn(
            tmp_path, root, home, "verification-IC",
            extra={"HIVE_VERIFICATION_MODEL": "env-vm"})
        assert self._model_from(argv["argv"]) == "env-vm"

    def test_non_verification_role_gets_no_model_override(self, tmp_path):
        root, home = self._scratch(tmp_path)
        argv = self._run_spawn(tmp_path, root, home, "IC")
        assert self._model_from(argv["argv"]) is None


class TestAgentSpawnPython:
    """The harness must run under the install's venv python.

    Regression: agent-spawn.sh used to exec bare `python3` from PATH. On a
    machine whose system python lacks httpx, every spawned agent died at
    turn 0 with "LLM unavailable: httpx not installed" while `hive doctor`
    — which checks the venv interpreter — reported everything green.
    The venv stub records its own invocation, so these tests prove the
    real agent-spawn.sh picked the venv's python (and that the fallback
    still works when no venv exists, e.g. a source checkout).
    """

    def _scratch(self, tmp_path, with_venv=True):
        root = tmp_path / "hive-root"
        (root / "lib" / "core").mkdir(parents=True)
        (root / "lib" / "core" / "agent-harness.py").write_text(
            "import sys, json\n"
            "print(json.dumps({'argv': sys.argv[1:]}))\n")
        # Run the REAL spawn script from inside the scratch install tree,
        # so the script's location-derived install dir resolves to it.
        (root / "lib" / "core" / "agent-spawn.sh").write_text(
            Path(f"{CORE_DIR}/agent-spawn.sh").read_text())
        marker = tmp_path / "venv-used"
        if with_venv:
            vbin = root / "venv" / "bin"
            vbin.mkdir(parents=True)
            vpy = vbin / "python"
            vpy.write_text(
                "#!/bin/sh\n"
                f"echo used >> '{marker}'\n"
                "exec python3 \"$@\"\n")
            os.chmod(vpy, 0o755)
        return root, marker

    def _run_spawn(self, tmp_path, root):
        home = tmp_path / "home"
        (home / ".config" / "hive").mkdir(parents=True)
        env = os.environ.copy()
        env.pop("HIVE_INSTALL_DIR", None)
        env.pop("HIVE_VERIFICATION_MODEL", None)
        env.update({
            "HIVE_ROOT": str(root),
            "HOME": str(home),
            "HIVE_ROLE": "IC",
            "COMPANY_DIR": str(tmp_path / "company"),
            # Do not source the real operator env file.
            "HIVE_ENV_FILE": str(tmp_path / "no-such-env.sh"),
        })
        return subprocess.run(
            ["bash", str(root / "lib" / "core" / "agent-spawn.sh"),
             "--", "test prompt"],
            capture_output=True, text=True, timeout=30, env=env)

    @staticmethod
    def _harness_ran(r):
        return any(line.startswith("{") for line in r.stdout.splitlines())

    def test_harness_runs_under_venv_python(self, tmp_path):
        root, marker = self._scratch(tmp_path, with_venv=True)
        r = self._run_spawn(tmp_path, root)
        assert r.returncode == 0, f"{r.stdout!r} {r.stderr!r}"
        assert self._harness_ran(r), f"harness did not run: {r.stdout!r}"
        assert marker.exists() and "used" in marker.read_text(), (
            "agent-spawn.sh did not exec the install's venv python — the "
            "harness ran under python3 on PATH, which may lack httpx "
            "(LLM/MCP calls silently disabled while `hive doctor` stays "
            "green)")

    def test_falls_back_to_python3_without_venv(self, tmp_path):
        root, marker = self._scratch(tmp_path, with_venv=False)
        r = self._run_spawn(tmp_path, root)
        assert r.returncode == 0, f"{r.stdout!r} {r.stderr!r}"
        assert self._harness_ran(r), f"harness did not run: {r.stdout!r}"
        assert not marker.exists()
        assert "WARNING" in r.stderr, (
            "expected the no-venv fallback to warn on stderr "
            f"(got: {r.stderr!r})")


class TestAllScriptsExist:
    """Ensure every .sh file in lib/tools/ is executable and parseable."""

    @pytest.mark.parametrize("script", [
        str(p) for p in Path(SCRIPTS_DIR).glob("*.sh")
    ])
    def test_script_is_executable(self, script):
        assert os.access(script, os.X_OK), f"{script} is not executable"

    @pytest.mark.parametrize("script", [
        str(p) for p in Path(SCRIPTS_DIR).glob("*.sh")
    ])
    def test_script_parses(self, script):
        """All scripts should parse without syntax errors."""
        result = subprocess.run(
            ["bash", "-n", script],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0, f"{script} has syntax errors: {result.stderr}"


class TestAllCoreScriptsParse:
    """Ensure every .sh file in lib/core/ parses without syntax errors."""

    @pytest.mark.parametrize("script", [
        str(p) for p in Path(CORE_DIR).glob("*.sh")
    ])
    def test_script_parses(self, script):
        result = subprocess.run(
            ["bash", "-n", script],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0, f"{script} has syntax errors: {result.stderr}"


class TestBuildPackageReproducibility:
    """A.1: the package build must be reproducible.

    Regression: build-package.sh used to pin every tarball mtime to the
    VERSION file's mtime, so cutting a release (which edits VERSION) changed
    the hash of an otherwise identical tree. The mtime anchor is now a stable
    per-release value (SOURCE_DATE_EPOCH > git commit date of VERSION > HEAD
    > epoch 0), so two builds of the same tree — even with different VERSION
    file mtimes — must produce byte-identical tarballs.
    """

    install_dir = Path(__file__).resolve().parent.parent

    def _build(self, out_dir, extra_env=None):
        env = os.environ.copy()
        if extra_env:
            env.update(extra_env)
        result = subprocess.run(
            ["bash", str(self.install_dir / "packaging" / "build-package.sh"),
             "--out-dir", str(out_dir)],
            capture_output=True, text=True, timeout=300, cwd=str(self.install_dir), env=env,
        )
        assert result.returncode == 0, f"build failed: {result.stderr}"
        tarballs = list(out_dir.glob("hive-*.tar.gz"))
        assert len(tarballs) == 1, f"expected one tarball, got {tarballs}"
        return hashlib.sha256(tarballs[0].read_bytes()).hexdigest()

    def test_touch_version_does_not_change_sha256(self, tmp_path):
        """Two builds of the same tree with different VERSION-file mtimes
        produce identical sha256."""
        h_a = self._build(tmp_path / "a")
        # Simulate cutting a release: VERSION is edited (mtime changes) and
        # committed. The mtime change alone must not move the hash.
        (self.install_dir / "VERSION").touch()
        h_b = self._build(tmp_path / "b")
        assert h_a == h_b, "tarball bytes changed after touching VERSION mtime"

    def test_source_date_epoch_is_respected(self, tmp_path):
        """An explicitly pinned SOURCE_DATE_EPOCH wins over git metadata and
        is itself reproducible."""
        h1 = self._build(tmp_path / "sde1", {"SOURCE_DATE_EPOCH": "1700000000"})
        h2 = self._build(tmp_path / "sde2", {"SOURCE_DATE_EPOCH": "1700000000"})
        assert h1 == h2

    def test_sidecar_matches_tarball(self, tmp_path):
        """The generated .sha256 sidecar must verify the tarball it sits next to."""
        out = tmp_path / "side"
        self._build(out)
        sidecars = list(out.glob("hive-*.sha256"))
        assert len(sidecars) == 1
        result = subprocess.run(
            ["sha256sum", "-c", str(sidecars[0])],
            capture_output=True, text=True, timeout=30, cwd=str(out),
        )
        assert result.returncode == 0, f"sidecar mismatch: {result.stdout}{result.stderr}"


class TestBuildPackagePublicSurface:
    """D.2: the tarball must ship the tracked public-surface files
    verbatim — no regenerated copies.

    Regression: build-package.sh used to regenerate INSTALL (re-pinning a
    version header the repo had de-pinned in B.2) and LICENSE (the
    truncated 324-byte MIT the repo had replaced with the full text in
    B.1) from hardcoded heredocs, so every published tarball contradicted
    the repo's own public surface. The build now copies the tracked
    files; this test builds for real and compares bytes.
    """

    install_dir = Path(__file__).resolve().parent.parent

    @classmethod
    def _build_and_extract(cls, tmp_path):
        out = tmp_path / "out"
        result = subprocess.run(
            ["bash", str(cls.install_dir / "packaging" / "build-package.sh"),
             "--out-dir", str(out)],
            capture_output=True, text=True, timeout=300,
            cwd=str(cls.install_dir), env=os.environ.copy(),
        )
        assert result.returncode == 0, f"build failed: {result.stderr}"
        tarballs = list(out.glob("hive-*.tar.gz"))
        assert len(tarballs) == 1, f"expected one tarball, got {tarballs}"
        extract = tmp_path / "x"
        extract.mkdir()
        result = subprocess.run(
            ["tar", "xzf", str(tarballs[0]), "-C", str(extract)],
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, f"extract failed: {result.stderr}"
        pkg = next(iter(extract.iterdir()))
        assert pkg.is_dir()
        return pkg

    def test_tarball_installs_the_tracked_install_doc(self, tmp_path):
        pkg = self._build_and_extract(tmp_path)
        shipped = (pkg / "INSTALL").read_text()
        tracked = (self.install_dir / "INSTALL").read_text()
        assert shipped == tracked, \
            "tarball INSTALL must be the tracked file verbatim (no " \
            "regenerated version-pinned header)"

    def test_tarball_ships_the_full_license(self, tmp_path):
        pkg = self._build_and_extract(tmp_path)
        shipped = (pkg / "LICENSE").read_text()
        tracked = (self.install_dir / "LICENSE").read_text()
        assert shipped == tracked, \
            "tarball LICENSE must be the tracked file verbatim"
        norm = re.sub(r"\s+", " ", shipped)
        assert ("IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE "
                "LIABLE") in norm, \
            "shipped license is missing the IN-NO-EVENT clause"

    def test_tarball_readme_has_the_acquisition_path(self, tmp_path):
        pkg = self._build_and_extract(tmp_path)
        readme = (pkg / "README.md").read_text()
        assert "Get Hive" in readme, \
            "tarball README must document how a stranger obtains Hive"
        assert "sha256sum -c" in readme, \
            "tarball README must document the sidecar verification step"

    def test_tarball_ships_the_full_public_surface(self, tmp_path):
        """B.1-B.3 added CONTRIBUTING.md, SECURITY.md and the 0.7.0
        CHANGELOG to the repo's public surface; a downloader who installs
        from the tarball (the path documented in the README) must get them
        too — shipped verbatim, like INSTALL and LICENSE."""
        pkg = self._build_and_extract(tmp_path)
        missing = []
        for name in ("CHANGELOG.md", "CONTRIBUTING.md", "SECURITY.md"):
            shipped_path = pkg / name
            tracked_path = self.install_dir / name
            assert tracked_path.is_file(), f"tracked {name} is missing"
            if not shipped_path.is_file():
                missing.append(name)
                continue
            assert shipped_path.read_text() == tracked_path.read_text(), \
                f"tarball {name} must be the tracked file verbatim"
        assert not missing, \
            f"tarball is missing public-surface files: {missing} — a " \
            "tarball install must carry the same public surface as the repo"

    def test_tarball_never_ships_python_build_leftovers(self, tmp_path):
        """The tarball must be free of __pycache__ dirs and .pyc/.pyo
        files even when the source tree is LIVE (a running suite keeps
        creating them in the same tree mid-build). Regression: the build
        staged with cp -r over the live tree and raced concurrent .pyc
        writers — "cp: cannot stat ...pyc.<tmp>" killed the 2026-11-04
        CI run. Staging now excludes those paths at copy time; this pins
        the shipped-tree invariant the fix exists to protect."""
        import importlib
        importlib.import_module("hive_cli")  # guarantees lib/hive_cli/__pycache__
        pkg = self._build_and_extract(tmp_path)
        leftovers = [str(p) for p in pkg.rglob("*")
                     if "__pycache__" in p.parts or p.suffix in (".pyc", ".pyo")]
        assert leftovers == [], \
            f"python build leftovers shipped in the tarball: {leftovers[:5]}"


class TestCompanyHealthRestartPolicy:
    """B.3: a failing daemon restarts up to HIVE_MAX_RESTARTS, is then
    marked dead IN processes.json (dead=true, dead_reason), and is never
    restarted again within the session. The dead state is what
    `hive status` / `hive doctor` / the dashboard surface to the operator.
    Runs the real supervisor loop against a scratch .company with a fast
    (1s) health interval."""

    @staticmethod
    def _dead_pid():
        """A pid that is definitely not running (pid > typical usage)."""
        pid = 4000000
        while pid > 1:
            try:
                os.kill(pid, 0)
                pid -= 1
            except ProcessLookupError:
                return pid
            except PermissionError:
                pid -= 1
        pytest.fail("no dead pid found")

    def test_failing_daemon_restarts_to_budget_then_dead_never_again(self, tmp_path):
        proj = tmp_path / "proj"
        company = proj / ".company"
        (company / "logs").mkdir(parents=True)
        # Item 5: start guards - the project is its own git repo root
        # and .company carries the init artifacts.
        subprocess.run(["git", "init", "-q"], cwd=str(proj), check=True)
        (company / "manifest.json").write_text('{"project_name": "test"}')
        dead_pid = self._dead_pid()
        # status-rebuild exits immediately without a departments/ dir, so
        # every supervised restart is a fresh failure — a perfect stand-in
        # for a daemon that cannot stay up.
        (company / "processes.json").write_text(json.dumps({
            "started_at": "2026-01-01T00:00:00Z",
            "project_root": str(proj),
            "processes": [
                {"pid": dead_pid, "role": "status-rebuild",
                 "department": "infrastructure", "interval": "continuous",
                 "started_at": "2026-01-01T00:00:00Z", "restart_count": 0},
            ],
        }))
        full_env = os.environ.copy()
        full_env["HIVE_INSTALL_DIR"] = str(Path(__file__).resolve().parent.parent)
        full_env["HIVE_MAX_RESTARTS"] = "2"
        full_env["HIVE_HEALTH_INTERVAL"] = "1"
        health = subprocess.Popen(
            ["bash", f"{SCRIPTS_DIR}/company-health.sh", str(proj)],
            env=full_env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            # 1) Budget exhausted: entry marked dead, restart_count == MAX.
            deadline = time.time() + 30
            entry = None
            while time.time() < deadline:
                data = json.loads((company / "processes.json").read_text())
                entry = data["processes"][0]
                if entry.get("dead"):
                    break
                time.sleep(0.25)
            assert entry is not None and entry.get("dead") is True, \
                f"daemon never marked dead: {entry}"
            assert entry.get("restart_count") == 2, entry
            assert "restart budget exhausted" in entry.get("dead_reason", ""), entry
            assert entry.get("dead_at"), entry

            # 2) Never restarted again: let several 1s cycles elapse and
            # require the entry to be byte-stable (no restart, no rewrites).
            snapshot = json.dumps(entry, sort_keys=True)
            time.sleep(3)
            data = json.loads((company / "processes.json").read_text())
            entry2 = data["processes"][0]
            assert json.dumps(entry2, sort_keys=True) == snapshot, \
                f"dead daemon was touched again: {entry} -> {entry2}"
            assert entry2.get("restart_count") == 2

            # 3) Exactly ONE terminal log line — not one per health cycle.
            log = (company / "logs" / "health.log").read_text()
            assert log.count("dead after 2 restarts") == 1
        finally:
            health.kill()
            health.wait(timeout=10)


class TestGatesRunSerialization:
    """B.4: per-ticket gate runs are flock-serialized. While a first run
    holds the ticket's lock, a second run on the SAME ticket skips
    (daemon mode, exit 0) or blocks (--wait, lifecycle mode). Uses an
    admin-dept ticket: its gate run auto-passes in milliseconds, so the
    serialization is what's under test — not gate evaluation."""

    TID = "TICKET-LOCK-1"

    @classmethod
    def _scratch(cls, tmp_path):
        proj = tmp_path / "proj"
        review = proj / ".company" / "departments" / "it" / "tickets" / "in-review"
        review.mkdir(parents=True)
        (proj / ".company" / "logs").mkdir(parents=True, exist_ok=True)
        (review / f"{cls.TID}.json").write_text(json.dumps({
            "id": cls.TID, "dept": "it", "type": "feature",
            "priority": "medium", "title": "t", "status": "in-review",
        }))
        return proj

    def _ticket(self, proj):
        return json.loads(
            (proj / ".company" / "departments" / "it" / "tickets" / "in-review"
             / f"{self.TID}.json").read_text())

    def _hold_lock(self, proj, secs=8):
        lock = proj / ".company" / "gate-runs" / f"it-{self.TID}.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        return lock, subprocess.Popen(
            ["flock", str(lock), "-c", f"sleep {secs}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    def test_second_run_skips_while_first_holds_lock(self, tmp_path):
        proj = self._scratch(tmp_path)
        lock, holder = self._hold_lock(proj)
        try:
            t0 = time.time()
            r = run_script(
                f"{SCRIPTS_DIR}/verification-gates.sh",
                ["it", str(proj), "--ticket", self.TID, "--quiet"],
                timeout=30,
            )
            elapsed = time.time() - t0
            assert r.returncode == 0, r.stderr
            assert elapsed < 4, f"skip should be immediate, took {elapsed:.1f}s"
            assert "another gate run is in progress" in r.stderr, r.stderr
            # A skipped run must not stamp the ticket.
            assert not self._ticket(proj).get("gates_passed")
        finally:
            holder.kill()
            holder.wait(timeout=10)

    def test_wait_mode_blocks_until_first_run_releases(self, tmp_path):
        proj = self._scratch(tmp_path)
        lock, holder = self._hold_lock(proj, secs=3)
        try:
            t0 = time.time()
            r = run_script(
                f"{SCRIPTS_DIR}/verification-gates.sh",
                ["it", str(proj), "--ticket", self.TID, "--wait", "--quiet"],
                timeout=30,
            )
            elapsed = time.time() - t0
            assert r.returncode == 0, r.stderr
            assert elapsed >= 2.0, \
                f"--wait run should have blocked ~3s, took {elapsed:.1f}s"
            # After the lock frees, the admin ticket auto-passes.
            assert self._ticket(proj).get("gates_passed") is True
        finally:
            holder.kill()
            holder.wait(timeout=10)


class TestCompanyStopReapsSupervisor:
    """B.4: `company stop` reaps the supervisor loop it started. Spawns
    the company-health wrapper exactly as company-start.sh does (a
    (while true; do ...; done) background subshell recorded in
    processes.json), plus one worker daemon, then runs the real
    company-stop.sh and asserts both are gone and processes.json is
    marked stopped."""

    @staticmethod
    def _is_dead(pid, timeout=10):
        """Dead = gone OR a zombie (terminated, awaiting reap). kill(0)
        succeeds on zombies, so the state must come from /proc."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                # comm may contain spaces — the state field follows the
                # last ')' in the "pid (comm) state ..." layout.
                state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
            except FileNotFoundError:
                return True
            except OSError:
                return False  # can't tell — don't declare death
            if state == "Z":
                return True
            time.sleep(0.1)
        return False

    def test_stop_kills_supervisor_loop_and_recorded_daemons(self, tmp_path):
        proj = tmp_path / "proj"
        company = proj / ".company"
        (company / "logs").mkdir(parents=True)
        # Item 5: start guards - the project is its own git repo root
        # and .company carries the init artifacts.
        subprocess.run(["git", "init", "-q"], cwd=str(proj), check=True)
        (company / "manifest.json").write_text('{"project_name": "test"}')
        # Mirror company-start.sh's supervisor spawn (1s cadence so the
        # wrapper visibly loops during the test window).
        launcher = tmp_path / "launcher.sh"
        launcher.write_text(f'#!/usr/bin/env bash\n'
                            f'(while true; do\n'
                            f'  bash "{SCRIPTS_DIR}/company-health.sh" "{proj}" '
                            f'>> "{company}/logs/health.log" 2>&1\n'
                            f'  sleep 1\n'
                            f'done) &\n'
                            f'echo $!\n'
                            f'wait\n')
        launcher.chmod(0o755)
        env = os.environ.copy()
        env["HIVE_INSTALL_DIR"] = str(Path(__file__).resolve().parent.parent)
        launcher_proc = subprocess.Popen(
            [str(launcher)], stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env=env,
        )
        wrapper_pid = int(launcher_proc.stdout.readline().strip())
        worker = subprocess.Popen(["sleep", "300"])
        try:
            time.sleep(0.5)  # let the wrapper start its first cycle
            (company / "processes.json").write_text(json.dumps({
                "started_at": "2026-01-01T00:00:00Z",
                "project_root": str(proj),
                "processes": [
                    {"pid": wrapper_pid, "role": "company-health",
                     "department": "infrastructure", "interval": "30s"},
                    {"pid": worker.pid, "role": "status-rebuild",
                     "department": "infrastructure", "interval": "continuous"},
                ],
            }))
            r = run_script(f"{SCRIPTS_DIR}/company-stop.sh", [str(proj)], timeout=60)
            assert r.returncode == 0, r.stderr
            assert self._is_dead(worker.pid), \
                f"recorded worker daemon survived company stop: {r.stdout}"
            assert self._is_dead(wrapper_pid), \
                f"supervisor loop survived company stop: {r.stdout}"
            data = json.loads((company / "processes.json").read_text())
            assert data["processes"] == []
            assert "stopped_at" in data
            m = re.search(r"Stopped (\d+) processes", r.stdout)
            assert m and int(m.group(1)) >= 2, r.stdout
        finally:
            for p in (worker, launcher_proc):
                p.kill()
                p.wait(timeout=10)





class TestCompanyStartNoJsonTraceback:
    """Regression: `hive start` (company-start.sh) printed a
    `json.decoder.JSONDecodeError: Expecting value: line 1 column 1
    (char 0)` traceback before every "Started X" line, and
    processes.json was never written by the script.

    Root cause: add_proc() built the in-memory process list with
    `echo "$PROCS" | python3 - <<'PYEOF' ... json.load(sys.stdin) ...`.
    A heredoc on the same command overrides the pipe's stdin, so python
    read its program from the heredoc and sys.stdin was already at EOF —
    json.load(sys.stdin) raised on every call, PROCS was left an empty
    string, and the final writer (json.loads(os.environ['CS_PROCS']))
    crashed too. company-stop/health/doctor then had no recorded PIDs.

    The state-file heredocs (roster/org-chart cleanup) had the same
    failure class one notch down: a 0-byte file (a rebuild killed
    mid-write by company-stop's kill -9) raised the identical
    "char 0" error, and an empty org-chart.json silently became a
    license to rmtree every department directory."""

    INFRA_DAEMONS = (
        "sprint-lifecycle", "roster-rebuild", "status-rebuild",
        "org-chart-rebuild", "worktree-merger", "verification-gates",
        "company-health",
    )

    @staticmethod
    def _run_company(script, proj, timeout=90):
        """Run a company script with FILE-based output capture.

        company-start.sh leaves background daemons running that inherit
        its stdout/stderr; with pipe capture, subprocess.run would block
        on pipe EOF until every daemon died. File redirection has no
        such dependency — the script itself exits promptly.
        """
        import tempfile
        out_path = tempfile.mktemp(prefix="company-start-test-", suffix=".out")
        err_path = tempfile.mktemp(prefix="company-start-test-", suffix=".err")
        try:
            env = os.environ.copy()
            env["HIVE_INSTALL_DIR"] = str(Path(__file__).resolve().parent.parent)
            # The A4 liveness check sleeps HIVE_START_GRACE (default 5 s)
            # before the summary; these tests spawn real, healthy daemons,
            # so 1 s keeps the suite fast while still exercising the check.
            env["HIVE_START_GRACE"] = "1"
            with open(out_path, "wb") as out_f, open(err_path, "wb") as err_f:
                proc = subprocess.Popen(
                    ["bash", f"{SCRIPTS_DIR}/{script}", str(proj)],
                    stdin=subprocess.DEVNULL, stdout=out_f, stderr=err_f,
                    env=env,
                )
                try:
                    proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=10)
                    raise
            stdout = Path(out_path).read_text(errors="replace")
            stderr = Path(err_path).read_text(errors="replace")
        finally:
            for p in (out_path, err_path):
                try:
                    os.unlink(p)
                except FileNotFoundError:
                    pass

        import types
        return types.SimpleNamespace(
            returncode=proc.returncode,
            stdout=stdout,
            stderr=stderr,
        )

    @pytest.fixture()
    def occupied_dashboard_and_mcp_ports(self):
        """Best-effort dummy listeners on 3456/7891 so company-start.sh
        takes the "already running — reusing it" paths and starts no
        dashboard/MCP of its own (which company-stop deliberately does
        not kill, so a test-started dashboard would leak). If a real
        dashboard/MCP already holds the port, the bind fails and we rely
        on that instead."""
        import http.server
        import threading

        class _Quiet(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok\n")

            def log_message(self, *a):
                pass

        servers = []
        for port in (3456, 7891):
            try:
                srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), _Quiet)
            except OSError:
                continue  # already in use — company-start will reuse it
            t = threading.Thread(target=srv.serve_forever, daemon=True)
            t.start()
            servers.append(srv)
        yield
        for s in servers:
            s.shutdown()
            s.server_close()

    def test_add_proc_does_not_read_stdin_of_a_heredoc(self):
        """The bug's exact shape: a python heredoc (stdin = program text,
        already consumed) calling json.load(sys.stdin)."""
        content = Path(SCRIPTS_DIR, "company-start.sh").read_text()
        assert "json.load(sys.stdin)" not in content, (
            "company-start.sh parses JSON from sys.stdin inside a "
            "heredoc — the heredoc overrides any pipe, so stdin is at "
            "EOF and every daemon start raises JSONDecodeError (char 0)"
        )

    def test_company_start_writes_processes_json_without_traceback(
            self, tmp_path, occupied_dashboard_and_mcp_ports):
        proj = tmp_path / "proj"
        company = proj / ".company"
        (company / "logs").mkdir(parents=True)
        # Item 5: start guards - the project is its own git repo root
        # and .company carries the init artifacts.
        subprocess.run(["git", "init", "-q"], cwd=str(proj), check=True)
        (company / "manifest.json").write_text('{"project_name": "test"}')
        (company / "departments" / "it").mkdir(parents=True)

        try:
            r = self._run_company("company-start.sh", proj)
            combined = r.stdout + r.stderr
            assert r.returncode == 0, combined
            assert "Traceback" not in combined, combined
            assert "JSONDecodeError" not in combined, combined

            procs_file = company / "processes.json"
            assert procs_file.exists(), "company-start.sh did not write processes.json"
            data = json.loads(procs_file.read_text())
            assert data["project_root"] == str(proj)
            procs = data["processes"]
            assert isinstance(procs, list) and len(procs) >= len(self.INFRA_DAEMONS)
            roles = {p.get("role") for p in procs}
            for role in self.INFRA_DAEMONS:
                assert role in roles, f"{role} missing from processes.json: {roles}"
            for p in procs:
                for key in ("pid", "role", "department", "interval", "started_at"):
                    assert key in p, f"process entry missing {key}: {p}"
                assert isinstance(p["pid"], int)
        finally:
            self._stop_company(proj, company)

    def _stop_company(self, proj, company):
        """Stop the company and belt-and-braces-kill anything recorded in
        processes.json, so a failure in this test (or in company-stop.sh)
        can never leave live daemons behind on the dev machine."""
        self._run_company("company-stop.sh", proj)
        import signal
        procs_file = company / "processes.json"
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

    def test_company_start_tolerates_empty_state_files(
            self, tmp_path, occupied_dashboard_and_mcp_ports):
        """0-byte roster.json/org-chart.json (writer killed mid-write)
        must not produce a traceback at startup, and an empty
        org-chart.json must NOT trigger deletion of department dirs."""
        proj = tmp_path / "proj"
        company = proj / ".company"
        (company / "logs").mkdir(parents=True)
        # Item 5: start guards - the project is its own git repo root
        # and .company carries the init artifacts.
        subprocess.run(["git", "init", "-q"], cwd=str(proj), check=True)
        (company / "manifest.json").write_text('{"project_name": "test"}')
        (company / "departments" / "it").mkdir(parents=True)
        (company / "departments" / "rogue-dept").mkdir()
        (company / "roster.json").write_text("")
        (company / "org-chart.json").write_text("")

        r = self._run_company("company-start.sh", proj)
        try:
            combined = r.stdout + r.stderr
            assert r.returncode == 0, combined
            assert "Traceback" not in combined, combined
            assert "JSONDecodeError" not in combined, combined
            assert (company / "departments" / "rogue-dept").exists(), (
                "empty org-chart.json must not cause department "
                "directories to be deleted"
            )
            data = json.loads((company / "processes.json").read_text())
            assert len(data["processes"]) >= len(self.INFRA_DAEMONS)
        finally:
            self._stop_company(proj, company)


class TestCompanyStartLiveness:
    """A4 (0.9.0): `hive start` proves the daemons are alive.

    company-start.sh used to report success the moment the PIDs were
    recorded; a daemon that died inside the grace window left a
    half-started company that only `hive status` revealed later (the
    0.8.4 processes.json sibling). The script now waits HIVE_START_GRACE
    (default 5 s) after spawning, verifies every recorded PID, and exits
    nonzero naming role + PID for each dead daemon.

    These tests run the real company-start.sh against a scratch install
    whose infra daemons are fakes: the alive fake loops on `sleep 1` and
    reaps itself when its parent subshell dies (a failed stop can never
    leak a loop), the dying fake exits immediately and turns into a
    zombie the script must not count as alive."""

    INFRA_DAEMONS = (
        "sprint-lifecycle", "roster-rebuild", "status-rebuild",
        "org-chart-rebuild", "worktree-merger", "verification-gates",
        "company-health",
    )

    ALIVE_FAKE = (
        "#!/bin/bash\n"
        "_ppid=$PPID\n"
        "while :; do\n"
        "  kill -0 \"$_ppid\" 2>/dev/null || exit 0\n"
        "  sleep 1\n"
        "done\n"
    )
    DYING_FAKE = "#!/bin/bash\nexit 1\n"

    @staticmethod
    def _free_port():
        import socket
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        return port

    def _scratch_install(self, tmp_path, dying=()):
        """A scratch install carrying the real company-start/stop scripts
        plus fake infra daemons (dying named in `dying`, alive otherwise).
        No venv, so the MCP server is not spawned (the scratch
        INSTALL_DIR has no venv/bin/python)."""
        install = tmp_path / "install"
        tools = install / "lib" / "tools"
        tools.mkdir(parents=True)
        for name in ("company-start.sh", "company-stop.sh", "hive-state.sh"):
            shutil.copy(Path(SCRIPTS_DIR) / name, tools / name)
        # G1: company-start/stop route .company/ JSON writes through the
        # state layer, so the scratch install must carry it (the real
        # install always has it).
        shutil.copytree(Path(SCRIPTS_DIR).parent / "hive_state",
                        install / "lib" / "hive_state")
        for name in self.INFRA_DAEMONS:
            fake = tools / f"{name}.sh"
            fake.write_text(self.DYING_FAKE if name in dying else self.ALIVE_FAKE)
        return install

    def _run_company(self, install, proj, timeout=120):
        """Run the scratch company-start.sh with FILE-based output capture
        (daemons inherit the script's stdout; pipe capture would block on
        EOF until every daemon died — same convention as
        TestCompanyStartNoJsonTraceback._run_company)."""
        import socket
        import tempfile
        import threading
        import types

        # Occupy an ephemeral dashboard port so company-start takes the
        # "already running — reusing it" path and starts no node of its
        # own (a test-started dashboard would leak: company-stop
        # deliberately does not kill it).
        dash_port = self._free_port()
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", dash_port))
        listener.listen(8)

        def _serve():
            while True:
                try:
                    conn, _ = listener.accept()
                    conn.close()
                except OSError:
                    return

        threading.Thread(target=_serve, daemon=True).start()

        # Never source the real ~/.config/hive/env.sh — isolate the env.
        env_file = tempfile.NamedTemporaryFile(
            prefix="company-start-liveness-env-", delete=False)
        env_file.close()

        out_path = tempfile.mktemp(prefix="company-start-liveness-", suffix=".out")
        err_path = tempfile.mktemp(prefix="company-start-liveness-", suffix=".err")
        proc = None
        try:
            env = os.environ.copy()
            env["HIVE_ROOT"] = str(install)
            env["HIVE_INSTALL_DIR"] = str(install)
            env["HIVE_DASHBOARD_PORT"] = str(dash_port)
            env["HIVE_MCP_PORT"] = str(self._free_port())  # nothing listens; no venv
            env["HIVE_START_GRACE"] = "1"  # dying fakes exit at t=0; 1 s is ample
            env["HIVE_ENV_FILE"] = env_file.name
            with open(out_path, "wb") as out_f, open(err_path, "wb") as err_f:
                proc = subprocess.Popen(
                    ["bash", str(install / "lib" / "tools" / "company-start.sh"),
                     str(proj)],
                    stdin=subprocess.DEVNULL, stdout=out_f, stderr=err_f,
                    env=env,
                )
                try:
                    proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=10)
                    raise
            stdout = Path(out_path).read_text(errors="replace")
            stderr = Path(err_path).read_text(errors="replace")
        finally:
            listener.close()
            for p in (env_file.name, out_path, err_path):
                try:
                    os.unlink(p)
                except FileNotFoundError:
                    pass

        return types.SimpleNamespace(
            returncode=proc.returncode, stdout=stdout, stderr=stderr)

    def _stop_company(self, install, proj):
        """Stop via the scratch company-stop.sh, then belt-and-braces
        SIGKILL anything still recorded in processes.json, so a failure
        here (or in company-stop.sh) can never leave live fakes behind."""
        import signal
        company = proj / ".company"
        procs_file = company / "processes.json"
        subprocess.run(
            ["bash", str(install / "lib" / "tools" / "company-stop.sh"), str(proj)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=60,
        )
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

    def _make_project(self, tmp_path):
        proj = tmp_path / "proj"
        (proj / ".company" / "logs").mkdir(parents=True)
        (proj / ".company" / "departments" / "it").mkdir(parents=True)
        # Item 5: start guards - own git repo root + init artifacts.
        subprocess.run(["git", "init", "-q"], cwd=str(proj), check=True)
        (proj / ".company" / "manifest.json").write_text(
            '{"project_name": "test"}')
        return proj

    def test_dying_daemon_fails_start_naming_role(self, tmp_path):
        install = self._scratch_install(tmp_path, dying=("sprint-lifecycle",))
        proj = self._make_project(tmp_path)
        try:
            r = self._run_company(install, proj)
            combined = r.stdout + r.stderr
            assert r.returncode != 0, (
                f"hive start must exit nonzero when a daemon dies in the "
                f"grace window, got {r.returncode}:\n{combined}"
            )
            assert "sprint-lifecycle" in r.stderr, (
                f"the dead daemon's role must be named on stderr: {r.stderr}"
            )
            assert "PID=" in r.stderr, (
                f"the dead daemon's PID must be named on stderr: {r.stderr}"
            )
            assert "Company started with" not in r.stdout, (
                "the success summary must not print when the start failed"
            )
            # processes.json is still written (before the check) so that
            # company-stop can clean up the surviving daemons.
            data = json.loads((proj / ".company" / "processes.json").read_text())
            roles = {p["role"] for p in data["processes"]}
            assert "sprint-lifecycle" in roles
            assert len(data["processes"]) == len(self.INFRA_DAEMONS)
        finally:
            self._stop_company(install, proj)

    def test_all_live_daemons_start_succeeds(self, tmp_path):
        install = self._scratch_install(tmp_path)
        proj = self._make_project(tmp_path)
        try:
            r = self._run_company(install, proj)
            combined = r.stdout + r.stderr
            assert r.returncode == 0, (
                f"healthy start must succeed: {combined}"
            )
            assert (f"Company started with {len(self.INFRA_DAEMONS)} processes"
                    ) in r.stdout, combined
            data = json.loads((proj / ".company" / "processes.json").read_text())
            assert len(data["processes"]) == len(self.INFRA_DAEMONS)
            for p in data["processes"]:
                assert os.kill(p["pid"], 0) is None, (
                    f"recorded daemon {p['role']} PID={p['pid']} is not alive"
                )
        finally:
            self._stop_company(install, proj)


class TestReleasePublishNoGhFallback:
    """release-publish.sh GO must actually publish when gh is unavailable.

    Regression: the no-gh branch printed 'falling back to local-only mode'
    but contained no fallback at all — the release record was left
    dangling (phase unchanged, no verdict, no ended_at), no artifact was
    placed anywhere, and the script exited 0 as if the publish had
    succeeded. The local-only publisher exists for exactly this path.
    """

    REPO = Path(__file__).resolve().parent.parent

    def _scratch(self, tmp_path):
        project = tmp_path / "project"
        (project / ".company" / "releases").mkdir(parents=True)
        record = project / ".company" / "releases" / "v0.9.0.json"
        record.write_text(json.dumps({"phase": "in-progress", "version": "v0.9.0"}))
        install = tmp_path / "install"
        (install / "lib" / "tools").mkdir(parents=True)
        (install / "lib" / "templates" / "publishers").mkdir(parents=True)
        (install / "dist").mkdir()
        shutil.copy(self.REPO / "lib" / "tools" / "gh-helpers.sh",
                    install / "lib" / "tools" / "gh-helpers.sh")
        # G1: release-publish.sh routes the record update through the state
        # layer, so the scratch install must carry it.
        shutil.copy(self.REPO / "lib" / "tools" / "hive-state.sh",
                    install / "lib" / "tools" / "hive-state.sh")
        shutil.copytree(self.REPO / "lib" / "hive_state",
                        install / "lib" / "hive_state")
        shutil.copy(self.REPO / "lib" / "templates" / "publishers" / "local-only.sh",
                    install / "lib" / "templates" / "publishers" / "local-only.sh")
        (install / "dist" / "hive-0.9.0.tar.gz").write_bytes(b"tarball")
        (install / "dist" / "hive-0.9.0.sha256").write_text("x  hive-0.9.0.tar.gz\n")
        # A gh that fails every invocation: gh_preflight's auth check trips
        # the no-gh branch without touching the network.
        bindir = tmp_path / "bin"
        bindir.mkdir()
        stub = bindir / "gh"
        stub.write_text("#!/bin/sh\nexit 1\n")
        stub.chmod(0o755)
        return project, install, bindir, record

    def test_go_without_gh_publishes_locally_and_updates_record(self, tmp_path):
        project, install, bindir, record = self._scratch(tmp_path)
        env = os.environ.copy()
        env["PATH"] = str(bindir) + os.pathsep + env["PATH"]
        env["HOME"] = str(tmp_path)  # isolate the host user config
        env["HIVE_CONFIG"] = str(tmp_path / "no-such-config.toml")
        env["PROJECT_ROOT"] = str(project)
        env["HIVE_ROOT"] = str(install)
        env.pop("GITHUB_TOKEN", None)
        r = subprocess.run(
            ["bash", str(self.REPO / "lib" / "tools" / "release-publish.sh"),
             "GO", "v0.9.0", "all gates green"],
            capture_output=True, text=True, timeout=30, env=env,
            cwd=str(project))
        combined = r.stdout + r.stderr
        assert r.returncode == 0, combined
        data = json.loads(record.read_text())
        assert data["phase"] == "done", data
        assert data["adversarial_verdict"] == "GO", data
        assert data.get("release_url"), data
        dest = project / "dist" / "releases" / "v0.9.0"
        assert (dest / "hive-0.9.0.tar.gz").exists(), combined


class TestPublisherTargetBranch:
    """publishers/github.sh must target the project's default branch, not a
    hard-coded 'main'. Regression: master-based and trunk-based projects
    got `gh release create --target main` — a branch that does not exist
    there — the same main/master portability class the worktree cleanup
    fixed. Precedence: [github].default_branch in the user config (explicit
    operator intent), then the remote's actual default branch, then main.
    """

    PUBLISHER = (Path(__file__).resolve().parent.parent /
                 "lib" / "templates" / "publishers" / "github.sh")

    def _git(self, cwd, *args):
        r = subprocess.run(["git", *args], cwd=str(cwd),
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        return r.stdout

    def _make_repo(self, tmp_path, branch):
        origin = tmp_path / "origin.git"
        proj = tmp_path / "proj"
        proj.mkdir()
        self._git(proj, "init", "-q", "-b", branch)
        self._git(proj, "config", "user.email", "t@example.com")
        self._git(proj, "config", "user.name", "test")
        (proj / "f.txt").write_text("x\n")
        self._git(proj, "add", ".")
        self._git(proj, "commit", "-qm", "init")
        subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
        self._git(proj, "remote", "add", "origin", str(origin))
        self._git(proj, "push", "-q", "-u", "origin", branch)
        return proj

    def _stub_gh(self, tmp_path, log, repo_view_answer):
        """A gh that records every invocation and answers the
        default-branch query with repo_view_answer (empty = no answer)."""
        bindir = tmp_path / "bin"
        bindir.mkdir(exist_ok=True)
        stub = bindir / "gh"
        # gh repo view -q .field prints the bare value (no JSON quotes)
        answer = repo_view_answer
        stub.write_text(
            "#!/bin/sh\n"
            f"echo \"$*\" >> \"{log}\"\n"
            f"if [ \"$1\" = \"repo\" ] && [ \"$2\" = \"view\" ]; then echo {answer}; fi\n"
            "exit 0\n")
        stub.chmod(0o755)
        return bindir

    def _run_publisher(self, tmp_path, proj, log, extra_env, bindir):
        tarball = tmp_path / "hive-9.9.9.tar.gz"
        tarball.write_bytes(b"tarball")
        sha = tmp_path / "hive-9.9.9.sha256"
        sha.write_text("x  hive-9.9.9.tar.gz\n")
        notes = tmp_path / "notes.md"
        notes.write_text("# Release 9.9.9\n")
        env = os.environ.copy()
        env["PATH"] = str(bindir) + os.pathsep + env["PATH"]
        # Isolate the host user config unless the test supplies its own.
        env.setdefault("HOME", str(tmp_path))
        env.update(extra_env)
        r = subprocess.run(
            ["bash", str(self.PUBLISHER), "9.9.9",
             str(tarball), str(sha), str(notes)],
            capture_output=True, text=True, timeout=30,
            cwd=str(proj), env=env)
        assert r.returncode == 0, r.stdout + r.stderr
        return log.read_text() if log.exists() else ""

    def test_config_default_branch_wins(self, tmp_path):
        proj = self._make_repo(tmp_path, "master")
        cfg = tmp_path / "cfg.toml"
        cfg.write_text('[github]\ndefault_branch = "master"\n')
        log = tmp_path / "gh.log"
        bindir = self._stub_gh(tmp_path, log, repo_view_answer="")
        out = self._run_publisher(
            tmp_path, proj, log,
            {"HIVE_CONFIG": str(cfg), "HOME": str(tmp_path)}, bindir)
        assert "--target master" in out, out

    def test_remote_default_branch_when_config_has_none(self, tmp_path):
        proj = self._make_repo(tmp_path, "trunk")
        log = tmp_path / "gh.log"
        bindir = self._stub_gh(tmp_path, log, repo_view_answer="trunk")
        out = self._run_publisher(
            tmp_path, proj, log,
            {"HIVE_CONFIG": str(tmp_path / "no-such-config.toml")}, bindir)
        assert "--target trunk" in out, out

    def test_falls_back_to_main_when_unknown(self, tmp_path):
        proj = self._make_repo(tmp_path, "master")
        log = tmp_path / "gh.log"
        bindir = self._stub_gh(tmp_path, log, repo_view_answer="")
        out = self._run_publisher(
            tmp_path, proj, log,
            {"HIVE_CONFIG": str(tmp_path / "no-such-config.toml")}, bindir)
        assert "--target main" in out, out


class TestReleaseGatesVersionGates:
    """The semver and changelog gates must handle the version under
    verification, not trip over it.

    Regression (a): the semver gate compared against the newest v* tag,
    which is the candidate itself whenever its tag already exists
    (re-runs, partial publishes that push the tag before the release)
    — every retry then failed 'not greater than previous tag v0.8.3'.
    Regression (b): the changelog gate grepped the v-prefixed version
    (the documented usage is '--version vX.Y.Z') against changelogs
    whose sections are headed '## X.Y.Z' — the gate could never pass.
    """

    def _make_repo(self, tmp_path, tags=(),
                   changelog="# Changelog\n\n## 0.9.0\nNotes for 0.9.0\n"):
        proj = tmp_path / "proj"
        proj.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=proj, check=True)
        subprocess.run(["git", "config", "user.email", "t@example.com"],
                       cwd=proj, check=True)
        subprocess.run(["git", "config", "user.name", "test"],
                       cwd=proj, check=True)
        (proj / "CHANGELOG.md").write_text(changelog)
        subprocess.run(["git", "add", "."], cwd=proj, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=proj, check=True)
        for t in tags:
            subprocess.run(["git", "tag", t], cwd=proj, check=True)
        return proj

    def _gate_line(self, tmp_path, proj, version, gate):
        r = run_script(
            f"{SCRIPTS_DIR}/release-gates.sh",
            ["--version", version, "--project-root", str(proj), "--dry-run"],
            timeout=60)
        lines = [l for l in r.stderr.splitlines()
                 if l.strip().startswith(gate + " ") or l.strip().startswith(gate + "\t")]
        assert lines, f"no {gate} line in: {r.stderr}"
        return lines[0]

    def test_semver_passes_when_own_tag_already_exists(self, tmp_path):
        proj = self._make_repo(tmp_path, tags=["v0.8.0", "v0.9.0"])
        line = self._gate_line(tmp_path, proj, "v0.9.0", "semver")
        assert "pass" in line

    def test_semver_still_rejects_lower_version(self, tmp_path):
        proj = self._make_repo(tmp_path, tags=["v0.9.0"])
        line = self._gate_line(tmp_path, proj, "v0.8.0", "semver")
        assert "fail" in line

    def test_changelog_passes_for_v_prefixed_version(self, tmp_path):
        proj = self._make_repo(tmp_path, tags=["v0.8.0"])
        line = self._gate_line(tmp_path, proj, "v0.9.0", "changelog")
        assert "pass" in line

    def test_changelog_still_fails_for_missing_section(self, tmp_path):
        proj = self._make_repo(tmp_path, tags=["v0.8.0"],
                               changelog="# Changelog\n\n## 0.8.0\nOld notes\n")
        line = self._gate_line(tmp_path, proj, "v0.9.0", "changelog")
        assert "fail" in line


class TestPublisherRunnerCwd:
    """release-publish-runner.sh must run the publisher from PROJECT_ROOT.

    Regression: publishers/local-only.sh places its artifacts under a
    path RELATIVE to the publisher's CWD and documents that
    'release-publish-runner cd's to PROJECT_ROOT' — but the runner only
    normalized the variable and never changed directory. Any launch of
    the sprint lifecycle from outside the project (manual runs, daemons
    started elsewhere) made local-only and cwd-sensitive custom
    publishers write dist/releases/... into the caller's directory.
    """

    REPO = Path(__file__).resolve().parent.parent

    def _scratch(self, tmp_path):
        project = tmp_path / "project"
        (project / ".company" / "sprints").mkdir(parents=True)
        install = tmp_path / "install"
        (install / "lib" / "tools").mkdir(parents=True)
        (install / "lib" / "templates" / "publishers").mkdir(parents=True)
        (install / "dist").mkdir()
        shutil.copy(self.REPO / "lib" / "tools" / "sprint-state.sh",
                    install / "lib" / "tools" / "sprint-state.sh")
        # G1: sprint-state.sh routes its writes through the state layer, so
        # the scratch install must carry it (the real install always does).
        shutil.copy(self.REPO / "lib" / "tools" / "hive-state.sh",
                    install / "lib" / "tools" / "hive-state.sh")
        shutil.copytree(self.REPO / "lib" / "hive_state",
                        install / "lib" / "hive_state")
        # G5: the runner resolves the publisher via config-get.sh, which
        # execs lib/hive_config.py — both must exist in the scratch install.
        shutil.copy(self.REPO / "lib" / "tools" / "config-get.sh",
                    install / "lib" / "tools" / "config-get.sh")
        shutil.copy(self.REPO / "lib" / "hive_config.py",
                    install / "lib" / "hive_config.py")
        local_only = (install / "lib" / "templates" / "publishers" / "local-only.sh")
        shutil.copy(self.REPO / "lib" / "templates" / "publishers" / "local-only.sh",
                    local_only)
        tarball = install / "dist" / "hive-0.9.0.tar.gz"
        tarball.write_bytes(b"tarball")
        sha = install / "dist" / "hive-0.9.0.sha256"
        sha.write_text("x  hive-0.9.0.tar.gz\n")
        (project / "CHANGELOG.md").write_text("# Changelog\n\n## 0.9.0\nNotes\n")
        state = project / ".company" / "sprints" / "current.json"
        state.write_text(json.dumps({
            "sprint_id": "sprint-test",
            "stage": "release-publish",
            "release": {
                "version": "0.9.0",
                "tarball": str(tarball),
                "sha256": str(sha),
                "publish_status": "pending",
            },
        }))
        cfg = tmp_path / "cfg.toml"
        cfg.write_text(f'[release]\npublisher_script = "{local_only}"\n')
        return project, install, cfg

    def test_local_only_publisher_writes_into_project_root(self, tmp_path):
        project, install, cfg = self._scratch(tmp_path)
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        env = os.environ.copy()
        env["HOME"] = str(tmp_path)
        env["HIVE_CONFIG"] = str(cfg)
        env["HIVE_ROOT"] = str(install)
        env.pop("GITHUB_TOKEN", None)
        r = subprocess.run(
            ["bash", str(self.REPO / "lib" / "tools" / "release-publish-runner.sh"),
             str(project)],
            capture_output=True, text=True, timeout=30, env=env,
            cwd=str(elsewhere))  # deliberately NOT the project root
        combined = r.stdout + r.stderr
        assert r.returncode == 0, combined
        dest = project / "dist" / "releases" / "0.9.0"
        assert (dest / "hive-0.9.0.tar.gz").exists(), (
            f"local-only publisher wrote relative to the caller's cwd\n{combined}")
        state = json.loads(
            (project / ".company" / "sprints" / "current.json").read_text())
        assert state["release"]["publish_status"] == "published", state


class TestSprintCostSummary:
    """B4 (0.9.0): the sprint archive writes the cost summary report.

    sprint-archive-done.sh (the archive path) now also runs
    sprint-cost-summary.sh, which prices every round in rounds/history/
    from the LLM usage log — the same windowing as B3 — and writes one
    markdown report to .company/reports/sprint-reviews/ via
    report-write.sh. The 'what did the sprint burn' answer is a file,
    not a grep: it exists, it parses, and its totals equal B2/B3's
    windowed sums on the same fixture."""

    REPO = Path(__file__).resolve().parent.parent
    FIXTURE_COMPANY = REPO / "tests" / "fixtures" / "company"
    SID = "sprint-20261103T000000Z"

    @staticmethod
    def _copy_company(tmp_path):
        project = tmp_path / "proj"
        company = project / ".company"
        shutil.copytree(TestSprintCostSummary.FIXTURE_COMPANY, company)
        return project, company

    @staticmethod
    def _archive(project, company, cwd=None):
        """Run the archive path. COMPANY_DIR/PROJECT_ROOT/HIVE_SPRINT_ID
        are stripped from the env either way: the scripts must resolve
        the company dir from the project-root argument themselves."""
        env = {k: v for k, v in os.environ.items()
               if k not in ("COMPANY_DIR", "PROJECT_ROOT", "HIVE_SPRINT_ID")}
        script = TestSprintCostSummary.REPO / "lib" / "tools" / "sprint-archive-done.sh"
        sid = TestSprintCostSummary.SID
        if cwd is None:
            return run_script(script, [str(project), sid], env=env, timeout=60)
        return subprocess.run(
            ["bash", str(script), str(project), sid],
            capture_output=True, text=True, timeout=60,
            env=env, cwd=str(cwd))

    @staticmethod
    def _parse_report(text):
        """(rows, totals) — rows are the table's data rows as lists of
        stripped cells, totals the '## Totals' key: value bullets."""
        rows = []
        for line in text.splitlines():
            if not line.startswith("|"):
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            if cells and cells[0] in ("round", "---"):
                continue
            rows.append(cells)
        totals = {}
        in_totals = False
        for line in text.splitlines():
            if line.startswith("## Totals"):
                in_totals = True
                continue
            if in_totals and line.startswith("- "):
                k, _, v = line[2:].partition(": ")
                totals[k.strip()] = v.strip()
        return rows, totals

    def test_archive_path_writes_parseable_cost_summary(self, tmp_path):
        project, company = self._copy_company(tmp_path)
        r = self._archive(project, company)
        assert r.returncode == 0, r.stdout + r.stderr
        report = company / "reports" / "sprint-reviews" / f"{self.SID}-cost-summary.md"
        assert report.exists(), "the archive path did not write the cost summary report"
        text = report.read_text()
        rows, totals = self._parse_report(text)
        assert [row[0] for row in rows] == ["round-008", "round-009"]
        r8, r9 = rows
        # per-round: entries, duration_s, tokens, cost_usd
        assert (r8[1], r8[2]) == ("4", "300")
        assert r8[3] == "300" and float(r8[4]) == pytest.approx(1.8e-05)
        assert (r9[1], r9[2]) == ("5", "600")
        assert r9[3] == "1200" and float(r9[4]) == pytest.approx(7.2e-05)
        # sprint totals — the windowed sums B2/B3 report for the same rounds
        assert totals["rounds"] == "2"
        assert totals["tokens"] == "1500"
        assert float(totals["cost_usd"]) == pytest.approx(9.0e-05)

    def test_report_written_to_project_company_not_cwd(self, tmp_path):
        """The archive runs from the project root in production, but a
        caller from elsewhere must not scatter the report: report-write.sh
        resolves the exported COMPANY_DIR, never a CWD-relative default."""
        project, company = self._copy_company(tmp_path)
        foreign = tmp_path / "elsewhere"
        foreign.mkdir()
        r = self._archive(project, company, cwd=foreign)
        assert r.returncode == 0, r.stdout + r.stderr
        report = company / "reports" / "sprint-reviews" / f"{self.SID}-cost-summary.md"
        assert report.exists(), "report missing from the project's .company"
        assert not (foreign / ".company").exists(), \
            "report-write.sh fell back to a CWD-relative .company"

    def test_rearchive_is_idempotent_and_matches_seeded_sample(self, tmp_path):
        """Re-running the archive rewrites the same file with the same
        content — deterministic, no appended duplicates — and the
        regenerated report is byte-identical to the seeded fixture
        sample (the expected shape, checked in once)."""
        project, company = self._copy_company(tmp_path)
        report = company / "reports" / "sprint-reviews" / f"{self.SID}-cost-summary.md"
        seeded = report.read_text()
        for _ in range(2):
            r = self._archive(project, company)
            assert r.returncode == 0, r.stdout + r.stderr
        text = report.read_text()
        assert text == seeded, "re-archive changed the report's content"
        rows, totals = self._parse_report(text)
        assert [row[0] for row in rows] == ["round-008", "round-009"]
        assert text.count(f"# Sprint cost summary — {self.SID}") == 1


class TestReleasePublishE2e:
    """D1 + D2 (0.9.0): the publish path, end-to-end against a stub gh.

    A scratch project (git repo + bare origin + .company sprint state
    with a release block + dist artifacts + a CHANGELOG section) runs
    release-publish-runner.sh — stage 8 of the sprint cycle — with a
    fake `gh` on PATH that records its argv. D1 asserts the full
    GO-verdict publish flow: the `gh release create` invocation carries
    the tag, the assets, and notes equal to the CHANGELOG section
    verbatim, and the record completes with the artifacts under the
    project root (the cwd class 0.8.4 fixed). D2 codifies the 0.8.4
    partial-publish retry: run 1 pushes the tag and then gh fails
    (record left dangling, no release); run 2 detects the existing tag
    (the semver gate excludes the candidate's own — the 0.8.4 fix) and
    completes the publish."""

    REPO = Path(__file__).resolve().parent.parent
    VER = "0.9.0"

    _GIT_IDENTITY = {
        "GIT_AUTHOR_NAME": "pub-test", "GIT_AUTHOR_EMAIL": "pub-test@example.com",
        "GIT_COMMITTER_NAME": "pub-test", "GIT_COMMITTER_EMAIL": "pub-test@example.com",
    }

    @classmethod
    def _git(cls, cwd, *args):
        r = subprocess.run(["git", *args], cwd=str(cwd),
                           env={**os.environ, **cls._GIT_IDENTITY},
                           capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, f"git {' '.join(args)}: {r.stderr}"
        return r

    @classmethod
    def _stub_gh(cls, bindir, fail_create_file):
        """Fake gh: records every invocation to $GH_STUB_LOG; `release
        create` fails while $fail_create_file exists, and a successful
        create drops a marker in $GH_STUB_RELEASE_DIR (the release
        'exists'). --notes-file content is copied to $GH_STUB_NOTES_OUT
        so the test can verify the notes verbatim."""
        bindir = Path(bindir)
        bindir.mkdir(parents=True, exist_ok=True)
        gh = bindir / "gh"
        gh.write_text(f"""#!/bin/sh
LOG="${{GH_STUB_LOG:?}}"; FAIL="{fail_create_file}"; RD="${{GH_STUB_RELEASE_DIR:-/nonexistent}}"
echo "$*" >> "$LOG"
if [ "$1 $2" = "auth status" ]; then exit 0; fi
if [ "$1 $2" = "repo view" ]; then exit 1; fi
if [ "$1 $2" = "release view" ]; then
  [ -f "$RD/$3" ] && exit 0 || exit 1
fi
if [ "$1 $2" = "release create" ]; then
  NF=""; PREV=""
  for a in "$@"; do
    if [ "$PREV" = "--notes-file" ]; then NF="$a"; fi
    PREV="$a"
  done
  if [ -n "$NF" ] && [ -f "$NF" ] && [ -n "${{GH_STUB_NOTES_OUT:-}}" ]; then
    cp "$NF" "$GH_STUB_NOTES_OUT"
  fi
  if [ -f "$FAIL" ]; then exit 1; fi
  mkdir -p "$RD"; touch "$RD/$3"; exit 0
fi
exit 0
""")
        gh.chmod(0o755)

    @classmethod
    def _make_project(cls, tmp_path):
        """Scratch project: git repo + bare origin, .company sprint state
        with a release block, dist artifacts, a CHANGELOG section."""
        proj = tmp_path / "proj"
        dist = proj / "dist"
        dist.mkdir(parents=True)
        with tarfile.open(dist / f"hive-{cls.VER}.tar.gz", "w:gz") as tf:
            src = tmp_path / "payload.txt"
            src.write_text("hive payload\n")
            tf.add(src, arcname="bin/payload.txt")
        digest = hashlib.sha256((dist / f"hive-{cls.VER}.tar.gz").read_bytes()).hexdigest()
        (dist / f"hive-{cls.VER}.sha256").write_text(f"{digest}  hive-{cls.VER}.tar.gz\n")
        section = ("The release notes body line one.\n"
                   "The release notes body line two.\n")
        (proj / "CHANGELOG.md").write_text(
            f"## {cls.VER}\n\n{section}\n## 0.8.9\n\nOlder release.\n")
        sprints = proj / ".company" / "sprints"
        sprints.mkdir(parents=True)
        (sprints / "current.json").write_text(json.dumps({
            "sprint_id": "s1", "stage": "release-publish",
            "release": {"version": cls.VER,
                        "tarball": f"dist/hive-{cls.VER}.tar.gz",
                        "sha256": f"dist/hive-{cls.VER}.sha256",
                        "publish_status": "pending"}}))
        cls._git(proj, "init", "-q", "-b", "main")
        # Repo-local identity: the publisher runs `git tag -a` inside a
        # nested subprocess that inherits no identity env. A clean machine
        # (CI runner, container) has no global git config and may not have
        # a resolvable hostname, so git cannot auto-detect one and the
        # tag fails. Repo config works in every environment — the same
        # hermetic pattern build_scratch_repo uses for the SPA fixtures.
        cls._git(proj, "config", "user.name", "pub-test")
        cls._git(proj, "config", "user.email", "pub-test@example.com")
        cls._git(proj, "add", "-A")
        cls._git(proj, "commit", "-qm", "base")
        origin = tmp_path / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
        cls._git(proj, "remote", "add", "origin", str(origin))
        cls._git(proj, "push", "-q", "origin", "main")
        return proj

    @classmethod
    def _run_publish(cls, proj, tmp_path, foreign_cwd, fail_create_file):
        env = {**os.environ,
               "HIVE_ROOT": str(cls.REPO),
               "HIVE_CONFIG": str(tmp_path / "no-such-config.toml"),
               "COMPANY_DIR": str(proj / ".company"),
               "GH_STUB_LOG": str(tmp_path / "gh-argv.log"),
               "GH_STUB_RELEASE_DIR": str(tmp_path / "gh-releases"),
               "GH_STUB_NOTES_OUT": str(tmp_path / "captured-notes.md"),
               "PATH": str(tmp_path / "bin") + os.pathsep + os.environ["PATH"]}
        return subprocess.run(
            ["bash", str(cls.REPO / "lib" / "tools" / "release-publish-runner.sh"),
             str(proj)],
            capture_output=True, text=True, timeout=120, env=env,
            cwd=str(foreign_cwd))

    @classmethod
    def _publish_status(cls, proj):
        state = json.loads((proj / ".company" / "sprints" / "current.json").read_text())
        return state["release"]["publish_status"]

    @classmethod
    def _create_line(cls, log_file):
        for line in log_file.read_text().splitlines():
            if line.startswith("release create"):
                return line
        return None

    def test_d1_go_verdict_publish_flow_with_stub_gh(self, tmp_path):
        """D1: the full GO-verdict publish flow against a stub gh — tag,
        assets, and verbatim CHANGELOG notes on the `gh release create`
        invocation; the record completes and the artifacts live under
        the project root even though the runner is invoked from a
        foreign cwd (the 0.8.4 cwd class)."""
        proj = self._make_project(tmp_path)
        self._stub_gh(tmp_path / "bin", tmp_path / "gh-fail")
        foreign = tmp_path / "foreign-cwd"
        foreign.mkdir()
        r = self._run_publish(proj, tmp_path, foreign, tmp_path / "gh-fail")
        assert r.returncode == 0, r.stdout + r.stderr
        line = self._create_line(tmp_path / "gh-argv.log")
        assert line is not None, "stub gh never saw a 'release create' call"
        parts = line.split()
        assert parts[2] == f"v{self.VER}", f"tag wrong in: {line}"
        assert f"dist/hive-{self.VER}.tar.gz" in line, "tarball asset missing"
        assert f"dist/hive-{self.VER}.sha256" in line, "sha256 asset missing"
        assert "--notes-file" in line, "notes file flag missing"
        notes = (tmp_path / "captured-notes.md").read_text()
        section = ("The release notes body line one.\n"
                   "The release notes body line two.\n")
        assert section in notes, \
            f"notes must carry the CHANGELOG section verbatim, got: {notes!r}"
        assert notes.startswith(f"# Release {self.VER}"), \
            "notes file must open with the release heading"
        assert self._publish_status(proj) == "published", \
            "the record must complete: publish_status=published"
        # Artifacts under the PROJECT root, not the caller's cwd:
        assert (proj / "dist" / f"hive-{self.VER}.tar.gz").is_file()
        cwd_artifacts = [p.name for p in foreign.iterdir()
                         if p.name.startswith("hive-")]
        assert cwd_artifacts == [], \
            f"artifacts leaked into the caller's cwd: {cwd_artifacts}"

    def test_d2_partial_publish_retry(self, tmp_path):
        """D2 (0.9.0): the partial-publish retry, codified. Run 1 pushes the
        tag, then the stub gh fails before the release exists (record left
        dangling); run 2 detects the existing tag and completes the
        publish — the semver gate excludes the candidate's own tag, the
        0.8.4 fix that makes the retry legal."""
        repo = Path(__file__).resolve().parent.parent
        proj = self._make_project(tmp_path)
        ver = type(self).VER
        v = "v" + ver
        fail_flag = tmp_path / "gh-fail"
        bindir = tmp_path / "bin2"
        self._stub_gh(bindir, str(fail_flag))
        foreign = tmp_path / "run2"
        foreign.mkdir()

        def run_publish():
            env = {**os.environ,
                   "HIVE_ROOT": str(repo),
                   "HIVE_CONFIG": str(tmp_path / "no-config.toml"),
                   "PATH": str(bindir) + os.pathsep + os.environ["PATH"],
                   "GH_STUB_LOG": str(tmp_path / "gh.log"),
                   "GH_STUB_RELEASE_DIR": str(tmp_path / "gh-rel")}
            return subprocess.run(
                ["bash", str(repo / "lib" / "tools" / "release-publish-runner.sh"),
                 str(proj)],
                capture_output=True, text=True, timeout=120,
                cwd=str(foreign), env=env)

        # run 1: tag pushed to origin, gh fails before the release exists
        fail_flag.write_text("x")
        r1 = run_publish()
        assert r1.returncode != 0, "run 1 must fail (gh create failed)"
        rec = json.loads((proj / ".company" / "sprints" / "current.json").read_text())
        assert rec["release"]["publish_status"] == "failed", \
            "run 1 leaves the record dangling (failed), not published"
        tags = self._git(proj, "tag", "-l").stdout.split()
        assert tags == [v], f"run 1 must have created the tag: {tags}"
        origin_tags = subprocess.run(
            ["git", "tag", "-l"], cwd=str(tmp_path / "origin.git"),
            capture_output=True, text=True, timeout=30).stdout.split()
        assert v in origin_tags, "the tag must be pushed to origin"
        assert not list((tmp_path / "gh-rel").glob("v*")), \
            "no release may exist after run 1"

        # the retry is legal: with its own tag present the semver gate
        # still passes (0.8.4: it excludes the candidate's own tag)
        gates = subprocess.run(
            ["bash", str(repo / "lib" / "tools" / "release-gates.sh"),
             "--version", v, "--project-root", str(proj), "--dry-run"],
            capture_output=True, text=True, timeout=120)
        semver_lines = [l for l in (gates.stdout + gates.stderr).splitlines()
                        if "semver" in l]
        assert any("pass" in l for l in semver_lines), \
            f"semver gate must pass with its own tag present: {semver_lines}"

        # run 2: same flow, gh works — the publish completes
        fail_flag.unlink()
        r2 = run_publish()
        assert r2.returncode == 0, \
            f"run 2 must complete the publish:\n{r2.stdout}\n{r2.stderr}"
        rec2 = json.loads((proj / ".company" / "sprints" / "current.json").read_text())
        assert rec2["release"]["publish_status"] == "published"
        assert list((tmp_path / "gh-rel").glob("v*")), \
            "the release must exist after run 2"
        tags2 = self._git(proj, "tag", "-l").stdout.split()
        assert tags2 == [v], "run 2 must not create a duplicate tag"


class TestCleanScratchScript:
    """T4 (0.14) — the workspace reclaims its own scratch.

    packaging/clean-scratch.sh applies the retention policy to the
    scratch under TMPDIR: the pytest basetemp (pytest-of-<user>/)
    keeps only the newest N generations, and the mktemp-shaped litter
    (tmp.*/hive-*/pip-unpack-*) is reaped past a min age — always
    dry-run-first, never touching fresh (live-session) paths."""

    install_dir = Path(__file__).resolve().parent.parent
    script = install_dir / "packaging" / "clean-scratch.sh"

    def test_script_exists_and_executable(self):
        assert self.script.is_file()
        assert os.access(self.script, os.X_OK), \
            "clean-scratch.sh must be executable"

    def test_usage_help(self):
        r = subprocess.run(
            ["bash", str(self.script), "--help"],
            capture_output=True, text=True, timeout=30)
        assert r.returncode == 0
        assert "Usage:" in r.stdout
        assert "--dry-run" in r.stdout

    @staticmethod
    def _mock(tmp_path):
        mock = tmp_path / "mocktmp"
        base = mock / "pytest-of-t"
        base.mkdir(parents=True)
        gens = {}
        for n in range(1, 7):
            d = base / f"pytest-{n}"
            d.mkdir()
            gens[n] = d
        gw = base / "pytest-5-gw0"
        gw.mkdir()
        (base / "pytest-current").write_text("link")
        old = time.time() - 86400 * 10
        for n in (1, 2, 3, 4, 5):
            os.utime(gens[n], (old, old))
        os.utime(gw, (old, old))
        for name in ("tmp.old", "hive-old", "pip-unpack-old"):
            d = mock / name
            d.mkdir()
            os.utime(d, (old, old))
        fresh = mock / "hive-new"
        fresh.mkdir()
        return mock, gens, fresh

    @staticmethod
    def _run(mock, extra=None):
        env = {**os.environ, "TMPDIR": str(mock)}
        return subprocess.run(
            ["bash", str(TestCleanScratchScript.script)] + (extra or []),
            capture_output=True, text=True, timeout=120, env=env)

    def test_dry_run_names_targets_removes_nothing(self, tmp_path):
        mock, gens, fresh = self._mock(tmp_path)
        r = self._run(mock, ["--dry-run"])
        assert r.returncode == 0, r.stdout + r.stderr
        assert "would remove" in r.stdout
        for n in (1, 2, 3):
            assert gens[n].exists(), f"dry-run must not remove pytest-{n}"
        assert (mock / "tmp.old").exists()
        assert "dry-run: 1" in r.stdout

    def test_real_run_applies_the_policy(self, tmp_path):
        mock, gens, fresh = self._mock(tmp_path)
        r = self._run(mock)
        assert r.returncode == 0, r.stdout + r.stderr
        for n in (1, 2, 3):
            assert not gens[n].exists(), \
                f"old generation pytest-{n} must be reaped"
        for n in (4, 5, 6):
            assert gens[n].exists(), \
                f"one of the newest 3 generations (pytest-{n}) must be kept"
        assert (mock / "pytest-of-t" / "pytest-5-gw0").exists(), \
            "an xdist worker dir rides with its generation (kept)"
        assert (mock / "hive-new").exists(), "fresh litter must be kept"
        for name in ("tmp.old", "hive-old", "pip-unpack-old"):
            assert not (mock / name).exists(), f"old litter {name} must be reaped"
        assert (mock / "pytest-of-t" / "pytest-current").exists(), \
            "pytest-current is never touched"


class TestAtomicStateWrites:
    """T3 (0.15) — the commit-point class, lib side.

    Every cross-process-observed state write in lib/tools + lib/core
    commits at a single point (tmp + rename): a reader observes the
    old content or the new content — never an empty file, never a
    partial line. The audit table behind this (and the static guard,
    tests/test_xdist_contract.py::TestLibCommitPointGuard) lives in
    docs/commit-point-audit.md. These tests exercise the converted
    writers end-to-end: the file lands complete, and no tmp litter is
    left behind.
    """

    def test_report_write_lands_atomic(self, tmp_company_dir):
        r = run_script(
            f"{SCRIPTS_DIR}/report-write.sh",
            ["marketing", "r.md", "--content", "hello report"],
            env={"COMPANY_DIR": str(tmp_company_dir)},
        )
        assert r.returncode == 0, r.stdout + r.stderr
        target = tmp_company_dir / "marketing" / "reports" / "r.md"
        assert target.read_text().strip() == "hello report"
        self._no_tmp_litter(tmp_company_dir)

    def test_test_results_lands_atomic(self, tmp_company_dir):
        r = run_script(
            f"{SCRIPTS_DIR}/test-results.sh",
            ["it", "web", "--format", "json", "--content", '{"ok": true}'],
            env={"COMPANY_DIR": str(tmp_company_dir)},
        )
        assert r.returncode == 0, r.stdout + r.stderr
        target = (tmp_company_dir / "departments" / "it" / "components"
                  / "web" / "test-results.json")
        assert target.read_text().strip() == '{"ok": true}'
        self._no_tmp_litter(tmp_company_dir)

    def test_release_verdict_lands_atomic(self, tmp_company_dir):
        state = tmp_company_dir / "sprints" / "current.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        verify_log = tmp_company_dir / "releases" / "vX-verify.md"
        state.write_text(json.dumps(
            {"stage": "release-verify",
             "release": {"verify_log": str(verify_log)}}))
        r = run_script(
            f"{SCRIPTS_DIR}/release-verdict-write.sh",
            ["GO", "--rationale", "all clean"],
            env={"COMPANY_DIR": str(tmp_company_dir)},
        )
        assert r.returncode == 0, r.stdout + r.stderr
        assert verify_log.read_text().splitlines()[0] == "VERDICT: GO"
        self._no_tmp_litter(tmp_company_dir)

    def test_research_findings_fresh_lands_atomic(self, tmp_company_dir):
        r = run_script(
            f"{SCRIPTS_DIR}/research-findings.sh",
            ["ticket-1", "--content", "finding one"],
            env={"COMPANY_DIR": str(tmp_company_dir)},
        )
        assert r.returncode == 0, r.stdout + r.stderr
        target = (tmp_company_dir / "research" / "ongoing"
                  / "ticket-1-findings.md")
        assert target.read_text().strip() == "finding one"
        # the append branch keeps O_APPEND (>>) — it must still work
        r2 = run_script(
            f"{SCRIPTS_DIR}/research-findings.sh",
            ["ticket-1", "--content", "finding two", "--append"],
            env={"COMPANY_DIR": str(tmp_company_dir)},
        )
        assert r2.returncode == 0, r2.stdout + r2.stderr
        assert target.read_text().splitlines() == [
            "finding one", "finding two"]
        self._no_tmp_litter(tmp_company_dir)

    @staticmethod
    def _no_tmp_litter(company_dir: Path):
        leftovers = []
        for p in company_dir.rglob("*.tmp.*"):
            leftovers.append(str(p))
        assert not leftovers, \
            f"tmp litter left behind the rename: {leftovers}"
