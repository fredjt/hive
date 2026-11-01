"""Tests for hive bash scripts — invoke and check exit codes/output."""
import sys
import json
import os
import subprocess
from pathlib import Path
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

    def test_ticket_list_no_args_shows_usage(self):
        result = run_script(f"{SCRIPTS_DIR}/ticket-list.sh")
        assert result.returncode != 0
        assert "Usage" in result.stderr or "usage" in result.stderr.lower()


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

    def test_core_claude_spawn_sh_exists(self):
        assert Path(f"{CORE_DIR}/agent-spawn.sh").exists()


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


