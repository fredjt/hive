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


def make_company(root: Path, ticket_id: str, dept: str = ENGINEERING_DEPT):
    company = root / ".company"
    for status in ["backlog", "sprint-current", "in-progress", "in-review", "done"]:
        (company / "departments" / dept / "tickets" / status).mkdir(parents=True)
    ticket = {
        "id": ticket_id,
        "title": "Gate test ticket",
        "description": "used by verification-gates tests",
        "status": "in-review",
        "type": "feature",
        "priority": "medium",
    }
    (company / "departments" / dept / "tickets" / "in-review" / f"{ticket_id}.json").write_text(
        json.dumps(ticket, indent=2)
    )
    return company


def run_gates(project: Path, dept: str, *extra):
    return subprocess.run(
        ["bash", str(GATES), dept, str(project), *extra],
        capture_output=True, text=True, timeout=120,
    )


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
