"""Behavioral tests for verification-gates.sh.

Runs the real gate script against temporary git projects + .company
workspaces and asserts on exit codes and the per-ticket gates.json
records it writes.
"""
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

INSTALL_DIR = Path(__file__).resolve().parent.parent
GATES = INSTALL_DIR / "lib" / "tools" / "verification-gates.sh"


@pytest.fixture()
def space_free_proj():
    """Project root under /tmp (no spaces in the path). Test projects that
    must pass the code gates regardless of where the hive checkout lives
    (checkout paths may contain spaces — see TestPathQuoting for that)."""
    d = Path(tempfile.mkdtemp(prefix="hive-gates-", dir="/tmp"))
    proj = d / "proj"
    yield proj
    shutil.rmtree(d, ignore_errors=True)


def run_git(cwd, *args):
    r = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=30
    )
    assert r.returncode == 0, f"git {' '.join(args)} failed: {r.stderr}"
    return r.stdout


def make_code_project(root: Path, ticket_id: str, passing_tests: bool = True):
    """Create a git project with a feature worktree for ticket_id whose
    branch has a [TDD-RED] commit touching a test file.
    Returns the worktree path."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "README.md").write_text("proj\n")
    run_git(root, "init", "-b", "main")
    run_git(root, "config", "user.email", "test@test.local")
    run_git(root, "config", "user.name", "Test")
    run_git(root, "add", "-A")
    run_git(root, "commit", "-m", "initial")

    wt = root / ".worktrees" / ticket_id
    run_git(root, "worktree", "add", "-b", f"feature/{ticket_id}", str(wt))

    passing = "assert 1 + 1 == 2" if passing_tests else "assert 1 + 1 == 3"
    (wt / "pytest.ini").write_text("[pytest]\n")
    (wt / f"test_{ticket_id.lower()}.py").write_text(
        f"def test_{ticket_id.lower()}_works():\n    {passing}\n"
    )
    run_git(wt, "add", "-A")
    run_git(wt, "commit", "-m", f"[TDD-RED] {ticket_id}: add failing test")
    return wt


# NOTE: use a dynamic (non-admin) department — per dept-helpers.sh the fixed
# departments (hr/it/rd/marketing/legal/finance) are planning-only, and
# verification-gates auto-passes admin-dept tickets without running code gates.
ENGINEERING_DEPT = "backend"


def make_company(root: Path, ticket_id: str, dept: str = ENGINEERING_DEPT,
                 work_kind: str | None = None):
    company = root / ".company"
    for status in ["backlog", "sprint-current", "in-progress", "in-review", "done"]:
        (company / "departments" / dept / "tickets" / status).mkdir(parents=True, exist_ok=True)
    ticket = {
        "id": ticket_id,
        "title": "Gate test ticket",
        "description": "used by verification-gates tests",
        "status": "in-review",
        "type": "feature",
        "priority": "medium",
    }
    if work_kind is not None:
        ticket["work_kind"] = work_kind
    (company / "departments" / dept / "tickets" / "in-review" / f"{ticket_id}.json").write_text(
        json.dumps(ticket, indent=2)
    )
    return company


def run_gates(project: Path, dept: str, *extra):
    return subprocess.run(
        ["bash", str(GATES), dept, str(project), *extra],
        capture_output=True, text=True, timeout=120,
    )


def make_deliverable_ticket(project: Path, ticket_id: str, dept: str = ENGINEERING_DEPT):
    """0.23: an in-review deliverable-kind ticket whose doer posted a
    valid summary — the light gate-passing setup (no git worktree
    needed). Replaces the old no-worktree auto-pass for tests that are
    not about the code gates."""
    make_company(project, ticket_id, dept=dept, work_kind="deliverable")
    memo = project / ".company" / "reports" / f"{ticket_id}.md"
    memo.parent.mkdir(parents=True, exist_ok=True)
    memo.write_text("# Memo\n\n## Plan\nDo the thing.\n")
    tfile = (project / ".company" / "departments" / dept / "tickets" /
             "in-review" / f"{ticket_id}.json")
    t = json.loads(tfile.read_text())
    t["comments"] = [{"author": "ic-admin", "timestamp": "t", "body":
                      "Delivered: the memo. Files written:\n"
                      f"- {memo.relative_to(project).as_posix()}\n"
                      "Follow-up tickets filed: none"}]
    tfile.write_text(json.dumps(t, indent=2))


class TestSecurityGate:
    """Gate 6 must SKIP when no security tool is available for the stack,
    matching the tests-pass / lint-pass gates. Hard-failing here bounced
    every code ticket on any project without bandit/pip-audit/cargo-audit/
    govulncheck installed."""

    def test_no_security_tool_is_a_skip_not_a_failure(self, space_free_proj):
        project = space_free_proj
        make_code_project(project, "T1")
        make_company(project, "T1")

        # Worktree has pytest.ini but no pyproject.toml / requirements.txt /
        # package.json, so detect_security_cmd has no branch to take — the
        # audit tool question never even arises regardless of what is
        # installed on the host.
        result = run_gates(project, ENGINEERING_DEPT)
        assert result.returncode == 0, (
            f"expected pass, got rc={result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        gates_file = (
            project / ".company" / "departments" / ENGINEERING_DEPT / "components" / "T1" / "gates.json"
        )
        gates = json.loads(gates_file.read_text())
        assert gates["gates"]["security-clean"]["status"] == "skip"
        # Every other code gate actually ran and passed
        assert gates["gates"]["worktree-exists"]["status"] == "pass"
        assert gates["gates"]["has-commits"]["status"] == "pass"
        assert gates["gates"]["tdd-order"]["status"] == "pass"
        assert gates["gates"]["tests-pass"]["status"] == "pass"
        assert gates["gates"]["lint-pass"]["status"] == "skip"

        ticket = json.loads(
            (project / ".company" / "departments" / ENGINEERING_DEPT / "tickets" / "in-review" / "T1.json")
            .read_text()
        )
        assert ticket["gates_passed"] is True

    def test_failing_tests_still_fail(self, space_free_proj):
        """Regression guard: the skip must not mask real gate failures."""
        project = space_free_proj
        make_code_project(project, "T2", passing_tests=False)
        make_company(project, "T2")

        result = run_gates(project, ENGINEERING_DEPT)
        assert result.returncode == 2
        gates_file = (
            project / ".company" / "departments" / ENGINEERING_DEPT / "components" / "T2" / "gates.json"
        )
        gates = json.loads(gates_file.read_text())
        assert gates["gates"]["tests-pass"]["status"] == "fail"


class TestPathQuoting:
    def test_worktree_path_with_space(self, tmp_path):
        """Project paths containing a space must not break the generated
        `cd <worktree> && pytest` command (it is run via bash -c)."""
        project = tmp_path / "my proj" / "inner dir"
        make_code_project(project, "T3")
        make_company(project, "T3")

        result = run_gates(project, ENGINEERING_DEPT)
        assert result.returncode == 0, (
            f"expected pass, got rc={result.returncode}\n"
            f"stderr: {result.stderr}"
        )
        ticket = json.loads(
            (project / ".company" / "departments" / ENGINEERING_DEPT
             / "tickets" / "in-review" / "T3.json").read_text()
        )
        assert ticket["gates_passed"] is True

class TestTicketTargeting:
    def test_empty_in_review_is_idle_not_error(self, space_free_proj):
        """No ticket in in-review is the normal idle state for the 90s
        daemon — exit 0, not exit 2 (exit 2 made the daemon log
        'restarting in 5s' every cycle for every idle dept)."""
        project = space_free_proj
        make_deliverable_ticket(project, "none")

        result = run_gates(project, ENGINEERING_DEPT)
        assert result.returncode == 0, result.stderr

    def test_named_ticket_is_the_one_gated(self, space_free_proj):
        project = space_free_proj
        make_deliverable_ticket(project, "A1")
        make_deliverable_ticket(project, "B2")  # second ticket in the same dept

        result = run_gates(project, ENGINEERING_DEPT, "--ticket", "B2")
        assert result.returncode == 0, result.stderr

        b2 = json.loads((project / ".company" / "departments" / ENGINEERING_DEPT
                         / "tickets" / "in-review" / "B2.json").read_text())
        a1 = json.loads((project / ".company" / "departments" / ENGINEERING_DEPT
                         / "tickets" / "in-review" / "A1.json").read_text())
        assert b2.get("gates_passed") is True
        assert a1.get("gates_passed") is not True

    def test_named_ticket_missing_is_an_error(self, space_free_proj):
        project = space_free_proj
        make_company(project, "A1")

        result = run_gates(project, ENGINEERING_DEPT, "--ticket", "NOPE")
        assert result.returncode == 2


class TestConcurrentGateRuns:
    """record_gate / mark_gates_passed write gates.json (and the ticket file)
    through a FIXED-named .tmp. The 90s gate daemon (company-start.sh) and
    the lifecycle's synchronous run can gate the same ticket at the same
    time; a shared tmp lets one process's mv steal the other's file and the
    loser dies with a non-zero rc, which the lifecycle reads as a spurious
    'usage error' gate failure (a bounce). The tmp must be per-process.

    The test makes the race deterministic: a PATH shim that makes every `mv`
    sleep 0.5s before moving. Without the per-ticket lock, both runs start
    the same evaluation — the first record_gate's mv consumes the shared
    tmp and kills the loser, and the concurrent pytest in the same worktree
    corrupts the winner's collection. With the lock, one run executes the
    full gate and the other skips cleanly; both exit 0 and the gate record
    is intact."""

    def test_two_concurrent_runs_both_succeed(self, tmp_path):
        # Ticket id without a hyphen: make_code_project turns it into a
        # Python identifier (test_<id>_works), so "TICK-1" would be a
        # SyntaxError the moment pytest collects it.
        proj = tmp_path / "proj"
        make_code_project(proj, "T4")
        make_company(proj, "T4")

        shim_dir = tmp_path / "shim"
        shim_dir.mkdir()
        real_mv = shutil.which("mv")
        assert real_mv, "system mv not found"
        (shim_dir / "mv").write_text(
            "#!/usr/bin/env bash\n"
            "sleep 0.5\n"
            f"exec {real_mv} \"$@\"\n"
        )
        (shim_dir / "mv").chmod(0o755)

        env = dict(os.environ, PATH=f"{shim_dir}:{os.environ['PATH']}")
        procs = [
            subprocess.Popen(
                ["bash", str(GATES), "backend", str(proj), "--ticket", "T4"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
            )
            for _ in range(2)
        ]
        rcs = [p.wait(timeout=300) for p in procs]
        out = b"".join(p.stdout.read() + p.stderr.read() for p in procs)

        assert rcs == [0, 0], (
            f"concurrent gate runs must both succeed, got rc={rcs} — a shared "
            f"fixed-named .tmp let one run steal the other's and the loser "
            f"crashed (the lifecycle reads that as a spurious gate failure)\n"
            f"{out.decode(errors='replace')[-800:]}"
        )
        # Exactly one run must have skipped on the busy lock — the loser
        # must NOT re-run the suite a second time (that's the double-pytest
        # in one worktree that corrupts collection).
        decoded = out.decode(errors="replace")
        assert "skipping (retry next cycle)" in decoded, (
            "the busy run did not skip — both runs evaluated the same "
            "ticket concurrently\n" + decoded[-800:]
        )

        # The record must survive the race intact.
        gates_file = (proj / ".company" / "departments" / "backend"
                      / "components" / "T4" / "gates.json")
        data = json.loads(gates_file.read_text())  # valid JSON or it raises
        assert data["gates"]["tests-pass"]["status"] == "pass"


class TestSkipTddRedFlag:
    """Regression: skip_tdd_red=true must NOT be a hard gate bounce.

    The flag means 'skip the tester; the engineer works the ticket'
    (ticket-bounce.sh --skip-tdd-red, the PR-merge-conflict rollback, and
    the 3-strike tester fallback all set it), and NOTHING clears it - it
    survives to done. A gate-0 bounce on it made every flagged ticket a
    permanent zombie: the engineer re-spawns, the ticket bounces, repeat,
    until the loop safety valve defers it - for every sprint, forever, and
    the ticket could never be verified. Flagged in-review tickets must be
    evaluated by the real gates (a branch with no test commit fails the
    TDD-order gate for the right reason)."""

    def test_flagged_ticket_with_green_tests_passes(self, space_free_proj):
        project = space_free_proj
        make_code_project(project, "T9")  # [TDD-RED] commit, tests pass
        make_company(project, "T9")

        tf = (project / ".company" / "departments" / ENGINEERING_DEPT
              / "tickets" / "in-review" / "T9.json")
        t = json.loads(tf.read_text())
        t["skip_tdd_red"] = True
        tf.write_text(json.dumps(t, indent=2))

        result = run_gates(project, ENGINEERING_DEPT)
        assert result.returncode == 0, (
            f"rc={result.returncode} - a skip_tdd_red ticket must be gated "
            f"on its actual work, not bounced on the flag\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        ticket = json.loads(tf.read_text())
        assert ticket["gates_passed"] is True

    def test_flagged_ticket_with_failing_tests_still_fails(self, space_free_proj):
        """Removing the flag bounce must not mask real gate failures."""
        project = space_free_proj
        make_code_project(project, "T10", passing_tests=False)
        make_company(project, "T10")

        tf = (project / ".company" / "departments" / ENGINEERING_DEPT
              / "tickets" / "in-review" / "T10.json")
        t = json.loads(tf.read_text())
        t["skip_tdd_red"] = True
        tf.write_text(json.dumps(t, indent=2))

        result = run_gates(project, ENGINEERING_DEPT)
        assert result.returncode == 2
        # A failing gate bounced the ticket out of in-review.
        ticket = json.loads((project / ".company" / "departments" / ENGINEERING_DEPT
                             / "tickets" / "in-progress" / "T10.json").read_text())
        assert ticket.get("gates_passed") is not True

    def test_already_gated_ticket_is_not_re_gated(self, space_free_proj):
        """Second run over an already-gates_passed ticket must be a no-op:
        no re-run of the gates, no duplicate 'gates passed' comment."""
        project = space_free_proj
        make_deliverable_ticket(project, "C1")

        first = run_gates(project, ENGINEERING_DEPT)
        assert first.returncode == 0, first.stderr
        ticket_path = (project / ".company" / "departments" / ENGINEERING_DEPT
                       / "tickets" / "in-review" / "C1.json")
        comments_after_first = len(json.loads(ticket_path.read_text()).get("comments", []))

        second = run_gates(project, ENGINEERING_DEPT)
        assert second.returncode == 0, second.stderr
        assert "already gates_passed" in second.stderr
        ticket = json.loads(ticket_path.read_text())
        assert ticket.get("gates_passed") is True
        assert len(ticket.get("comments", [])) == comments_after_first

class TestAdminDeptGates:
    """0.23: the name-based waiver is gone. The resolver (ticket
    work_kind → org-chart dept kind → legacy name list) decides which
    gate set a ticket runs: a code-kind ticket in a fixed dept runs the
    strict code gates; a deliverable-kind ticket runs the deliverable
    gates."""

    def test_code_kind_in_fixed_dept_runs_code_gates(self, space_free_proj):
        """0.23: the waiver is gone. An hr ticket that declares
        work_kind=code (the rd-prototype case) runs the STRICT code
        gates despite its fixed dept."""
        project = space_free_proj
        make_code_project(project, "T7", passing_tests=False)
        make_company(project, "T7", dept="hr", work_kind="code")

        result = run_gates(project, "hr")
        assert result.returncode == 2
        gates_file = (
            project / ".company" / "departments" / "hr" / "components" / "T7" / "gates.json"
        )
        gates = json.loads(gates_file.read_text())
        assert gates["gates"]["tests-pass"]["status"] == "fail"
        assert "admin-dept-ticket" not in gates.get("gates", {}), (
            "admin auto-pass skipped the code gates"
        )

    def test_deliverable_kind_in_fixed_dept_runs_deliverable_gates(
            self, space_free_proj):
        """0.23: a code-less hr ticket is deliverable-kind (the legacy
        name list) — it runs the deliverable gates, and with no summary
        comment the deliverable-named gate FAILS (the blind waiver is
        gone)."""
        project = space_free_proj
        make_company(project, "T8", dept="hr")

        result = run_gates(project, "hr")
        assert result.returncode == 2
        gates_file = (
            project / ".company" / "departments" / "hr" / "components" / "T8" / "gates.json"
        )
        gates = json.loads(gates_file.read_text())
        assert gates["gates"]["deliverable-named"]["status"] == "fail"
        t8 = json.loads((project / ".company" / "departments" / "hr" /
                         "tickets" / "in-progress" / "T8.json").read_text())
        assert any("Gate failure" in c.get("body", "") for c in t8["comments"])


class TestDeliverableGates:
    """W3 (0.23): the four deterministic deliverable gates.

    Input is the doer's summary comment — the prompt is the contract,
    the gate is the enforcement.
    """

    def _setup(self, project, tid, body=None, work_kind="deliverable",
               memo_text="# Memo\n\n## Plan\nDo the thing.\n"):
        project.mkdir(parents=True, exist_ok=True)
        (project / "README.md").write_text("proj\n")
        make_company(project, tid, dept="hr", work_kind=work_kind)
        memo = project / ".company" / "reports" / f"{tid}.md"
        memo.parent.mkdir(parents=True, exist_ok=True)
        memo.write_text(memo_text)
        if body is not None:
            tfile = (project / ".company" / "departments" / "hr" /
                     "tickets" / "in-review" / f"{tid}.json")
            t = json.loads(tfile.read_text())
            t["comments"] = [{"author": "ic-admin", "timestamp": "t", "body": body}]
            tfile.write_text(json.dumps(t, indent=2))
        return memo

    def test_pass_leg(self, space_free_proj):
        """A named, existing, clean deliverable → rc 0, gates_passed,
        four pass entries in the per-ticket record."""
        p = space_free_proj
        memo = self._setup(p, "D1")
        self._comment(p, "D1", memo)
        result = run_gates(p, "hr", "--ticket", "D1")
        assert result.returncode == 0, result.stderr
        gates = json.loads((p / ".company" / "departments" / "hr" / "components" /
                            "D1" / "gates.json").read_text())
        for g in ("deliverable-named", "deliverable-exists",
                  "no-placeholders", "followup-scoped"):
            assert gates["gates"][g]["status"] == "pass", (g, gates)
        t1 = json.loads((p / ".company" / "departments" / "hr" / "tickets" /
                         "in-review" / "D1.json").read_text())
        assert t1.get("gates_passed") is True

    def test_no_summary_comment_fails_named(self, space_free_proj):
        """No summary comment at all is a FAIL of deliverable-named, not
        a skip — the doer must name what it wrote, or it bounces."""
        p = space_free_proj
        self._setup(p, "D2", body=None)
        result = run_gates(p, "hr", "--ticket", "D2")
        assert result.returncode == 2
        gates = json.loads((p / ".company" / "departments" / "hr" / "components" /
                            "D2" / "gates.json").read_text())
        assert gates["gates"]["deliverable-named"]["status"] == "fail"
        t2 = json.loads((p / ".company" / "departments" / "hr" / "tickets" /
                         "in-progress" / "D2.json").read_text())
        assert any(c["body"].startswith("Gate failure") for c in t2["comments"])

    def _gates(self, p, tid):
        return json.loads((p / ".company" / "departments" / "hr" / "components" /
                           f"{tid}" / "gates.json").read_text())

    def test_missing_path_fails_exists(self, space_free_proj):
        p = space_free_proj
        self._setup(p, "D3", body=("Delivered: x. Files written:\n"
                                   "- .company/nope/ghost.md\n"
                                   "Follow-up tickets filed: none"))
        result = run_gates(p, "hr", "--ticket", "D3")
        assert result.returncode == 2
        g = self._gates(p, "D3")
        e = g["gates"]["deliverable-exists"]
        assert e["status"] == "fail" and "ghost.md" in e["detail"], e

    def test_empty_file_fails_exists(self, space_free_proj):
        p = space_free_proj
        memo = self._setup(p, "D4", memo_text="")
        self._comment(p, "D4", memo)
        result = run_gates(p, "hr", "--ticket", "D4")
        assert result.returncode == 2
        g = self._gates(p, "D4")
        assert g["gates"]["deliverable-exists"]["status"] == "fail"

    def test_one_of_two_missing_named_in_detail(self, space_free_proj):
        p = space_free_proj
        memo = self._setup(p, "D5")
        self._comment(p, "D5", memo, extra="- .company/nope/second.md")
        result = run_gates(p, "hr", "--ticket", "D5")
        assert result.returncode == 2
        g = self._gates(p, "D5")
        e = g["gates"]["deliverable-exists"]
        assert "second.md" in e["detail"] and memo.name not in e["detail"], e

    def test_placeholder_fails(self, space_free_proj):
        p = space_free_proj
        self._setup(p, "D6", memo_text="# Memo\n\n## Plan\nDo the thing. TBD\n")
        self._comment(p, "D6", p / ".company" / "reports" / "D6.md")
        result = run_gates(p, "hr", "--ticket", "D6")
        assert result.returncode == 2
        g = self._gates(p, "D6")
        n = g["gates"]["no-placeholders"]
        assert n["status"] == "fail" and "D6.md" in n["detail"], n

    def test_empty_section_heading_fails(self, space_free_proj):
        p = space_free_proj
        self._setup(p, "D7", memo_text="# Memo\n\n## Plan\nDo it.\n\n## Open\n\n## Done\nDone.\n")
        self._comment(p, "D7", p / ".company" / "reports" / "D7.md")
        result = run_gates(p, "hr", "--ticket", "D7")
        assert result.returncode == 2
        g = self._gates(p, "D7")
        assert g["gates"]["no-placeholders"]["status"] == "fail"

    def test_followup_nonexistent_fails(self, space_free_proj):
        p = space_free_proj
        memo = self._setup(p, "D8")
        self._comment(p, "D8", memo, followup="HR-9999")
        result = run_gates(p, "hr", "--ticket", "D8")
        assert result.returncode == 2
        g = self._gates(p, "D8")
        f = g["gates"]["followup-scoped"]
        assert f["status"] == "fail" and "HR-9999" in f["detail"], f

    def test_followup_real_ticket_passes(self, space_free_proj):
        p = space_free_proj
        memo = self._setup(p, "D9")
        other = p / ".company" / "departments" / "hr" / "tickets" / "backlog"
        (other / "HR-7777.json").write_text(json.dumps(
            {"id": "HR-7777", "status": "backlog"}))
        self._comment(p, "D9", memo, followup="HR-7777")
        result = run_gates(p, "hr", "--ticket", "D9")
        assert result.returncode == 0, result.stderr
        g = self._gates(p, "D9")
        assert g["gates"]["followup-scoped"]["status"] == "pass"

    def test_require_false_disables_gates(self, space_free_proj):
        p = space_free_proj
        self._setup(p, "D10", body=None)
        (p / ".company" / "gates.json").write_text(
            json.dumps({"require_deliverable_gates": False}))
        result = run_gates(p, "hr", "--ticket", "D10")
        assert result.returncode == 0, result.stderr
        g = self._gates(p, "D10")
        assert g["gates"]["deliverable-gates"]["status"] == "skip"

    def _comment(self, p, tid, memo, extra=None, followup="none"):
        lines = ["Delivered: the memo. Files written:",
                 f"- {memo.relative_to(p).as_posix()}"]
        if extra:
            lines.append(extra)
        lines.append(f"Follow-up tickets filed: {followup}")
        tfile = (p / ".company" / "departments" / "hr" / "tickets" /
                 "in-review" / f"{tid}.json")
        t = json.loads(tfile.read_text())
        t["comments"] = [{"author": "ic-admin", "timestamp": "t",
                          "body": "\n".join(lines)}]
        tfile.write_text(json.dumps(t, indent=2))
