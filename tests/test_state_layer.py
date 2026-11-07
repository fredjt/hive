"""G1 — one state layer for .company/ (RELEASE_GOALS 0.10.0).

Covers:
  * the state layer itself (atomic write, per-file lock, schema
    validation — a violating write fails loudly and nothing malformed
    lands on disk),
  * the ticket accessors (create/move/approve/bounce) — the same code
    path the ticket scripts, the MCP tools, and the lifecycle use,
  * the round activity log: a concurrency test proves a racing writer
    loses no entry and the file carries ONE schema,
  * a grep test: no bash script (or embedded python heredoc) in lib/
    writes a .company/ JSON file outside the state layer.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB = REPO_ROOT / "lib"
sys.path.insert(0, str(LIB))


def run_state(*args: str, company_dir: Path, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, "COMPANY_DIR": str(company_dir), "PYTHONPATH": str(LIB)}
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "hive_state", *args],
        capture_output=True, text=True, timeout=60, env=env,
    )


def parse(result: subprocess.CompletedProcess) -> dict:
    s = result.stdout.strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        i = s.find("{")
        if i >= 0:
            return json.loads(s[i:])
    raise AssertionError(f"no JSON in output: {result.stdout!r} stderr={result.stderr!r}")


@pytest.fixture
def company(tmp_path: Path) -> Path:
    """A minimal .company/ workspace."""
    c = tmp_path / ".company"
    (c / "departments" / "backend" / "tickets").mkdir(parents=True)
    (c / "sprints").mkdir()
    (c / "manifest.json").write_text(json.dumps(
        {"project_name": "T", "cycle_count": 0, "updated_at": "2026-01-01T00:00:00Z"}))
    (c / "sprints" / "current.json").write_text(json.dumps(
        {"sprint_id": "S-001", "stage": "ceo-plan"}))
    return c


# ── core primitives ────────────────────────────────────────────────

class TestStateLayerCore:
    def test_atomic_write_and_read(self, company):
        from hive_state.core import atomic_write, read_json

        atomic_write("manifest.json", {**json.loads((company / "manifest.json").read_text()),
                                       "project_name": "Renamed"}, company)
        assert read_json("manifest.json", company)["project_name"] == "Renamed"

    def test_schema_violation_fails_loudly_and_leaves_file_untouched(self, company):
        """A schema-violating write must fail loudly; nothing malformed
        lands on disk (G1 done-when #4)."""
        from hive_state.core import SchemaViolation, atomic_write

        # seed a valid ticket
        dept_dir = company / "departments" / "backend" / "tickets"
        (dept_dir / "backlog").mkdir(parents=True, exist_ok=True)
        valid = {
            "id": "BACKEND-0001", "type": "bug", "title": "t", "priority": "high",
            "status": "backlog", "created_at": "2026-01-01T00:00:00Z",
        }
        atomic_write("departments/backend/tickets/backlog/BACKEND-0001.json", valid, company)
        before = (dept_dir / "backlog" / "BACKEND-0001.json").read_bytes()

        # status field disagrees with its directory → violation
        bad = dict(valid, status="in-review")
        with pytest.raises(SchemaViolation):
            atomic_write("departments/backend/tickets/backlog/BACKEND-0001.json", bad, company)
        assert (dept_dir / "backlog" / "BACKEND-0001.json").read_bytes() == before

        # unknown type → violation, file untouched
        bad2 = dict(valid, type="not-a-type")
        with pytest.raises(SchemaViolation):
            atomic_write("departments/backend/tickets/backlog/BACKEND-0001.json", bad2, company)
        assert (dept_dir / "backlog" / "BACKEND-0001.json").read_bytes() == before

    def test_schema_violation_via_cli_exit_code(self, company):
        r = run_state("write", "departments/backend/tickets/backlog/BAD-0001.json",
                      "--json", json.dumps({"id": "BAD-0001", "type": "bogus", "title": "t",
                                            "priority": "high", "status": "backlog",
                                            "created_at": "2026-01-01T00:00:00Z"}),
                      company_dir=company)
        assert r.returncode == 4, r.stderr
        assert parse(r)["ok"] is False
        assert not (company / "departments" / "backend" / "tickets" / "backlog" / "BAD-0001.json").exists()

    def test_lock_blocks_a_second_writer(self, company):
        """Per-file flock: a second thread must wait, not clobber.

        (Same-thread re-acquisition is deliberately a no-op — accessors
        compose; cross-thread contention is what the lock guards.)
        """
        import threading
        from hive_state.core import CompanyError, locked

        rel = "sprints/current.json"
        got = threading.Event()

        def hold_then_release():
            with locked(company, rel):
                got.set()
                time.sleep(1.5)

        t = threading.Thread(target=hold_then_release)
        t.start()
        assert got.wait(5)
        try:
            locked(company, rel, timeout=0.5).__enter__()
            raised = False
        except CompanyError:
            raised = True
        finally:
            t.join()
        assert raised, "second writer should have timed out, not clobbered"


# ── ticket accessors (the shared code path) ────────────────────────

class TestTicketAccessors:
    def test_full_ticket_lifecycle_via_state_layer(self, company):
        """create → sprint-current → in-progress → in-review → approve
        (done) — the lifecycle still passes through the state layer."""
        r = run_state("ticket-create", "--dept", "backend", "--type", "feature",
                      "--priority", "high", "--title", "Add rate limiting",
                      "--desc", "rate limit middleware", "--created-by", "ceo",
                      "--source", "sprint-planning", company_dir=company)
        assert r.returncode == 0, r.stderr
        result = parse(r)
        tid = result["ticket_id"]
        assert re.fullmatch(r"BACKEND-\d{4}", tid)

        t = json.loads((company / "departments" / "backend" / "tickets" / "backlog" / f"{tid}.json").read_text())
        assert t["status"] == "backlog" and t["title"] == "Add rate limiting"

        for st in ("sprint-current", "in-progress"):
            r = run_state("ticket-move", "--dept", "backend", "--ticket", tid,
                          "--status", st, company_dir=company,
                          env_extra={"HIVE_SCRIPTED_MOVE": "1"})
            assert r.returncode == 0, r.stderr

        # claim sets the assignee (in-review requires one)
        r = run_state("ticket-claim", "--dept", "backend", "--ticket", tid,
                      "--role", "IC-Engineer", "--agent-id", "ic-1",
                      "--agent-name", "IC-Engineer (backend)", company_dir=company)
        assert r.returncode == 0, r.stderr

        r = run_state("ticket-move", "--dept", "backend", "--ticket", tid,
                      "--status", "in-review", company_dir=company,
                      env_extra={"HIVE_SCRIPTED_MOVE": "1"})
        assert r.returncode == 0, r.stderr

        # pass the deterministic gates, then approve (atomic)
        tfile = company / "departments" / "backend" / "tickets" / "in-review" / f"{tid}.json"
        t = json.loads(tfile.read_text())
        t["gates_passed"] = True
        tfile.write_text(json.dumps(t, indent=2))

        r = run_state("ticket-approve", "--dept", "backend", "--ticket", tid,
                      "--verifier", "Verification-IC",
                      "--rationale", "Verified logic, edge cases, and contract compliance end to end.",
                      company_dir=company)
        assert r.returncode == 0, r.stderr
        assert (company / "departments" / "backend" / "tickets" / "done" / f"{tid}.json").exists()
        done = json.loads((company / "departments" / "backend" / "tickets" / "done" / f"{tid}.json").read_text())
        assert done["verified_by"] == "Verification-IC"
        assert done["resolution"]["status"] == "implemented"

    def test_move_denied_exit_code_3(self, company):
        """Role policy: an unscripted agent move is denied with exit 3."""
        r = run_state("ticket-create", "--dept", "backend", "--type", "bug",
                      "--priority", "low", "--title", "t1", "--desc", "d",
                      company_dir=company)
        tid = parse(r)["ticket_id"]
        r = run_state("ticket-move", "--dept", "backend", "--ticket", tid,
                      "--status", "in-progress", company_dir=company,
                      env_extra={"HIVE_ROLE": "IC"})
        assert r.returncode == 3
        assert parse(r)["ok"] is False

    def test_ticket_not_found_exit_code_5(self, company):
        r = run_state("ticket-move", "--dept", "backend", "--ticket", "BACKEND-9999",
                      "--status", "in-progress", company_dir=company)
        assert r.returncode == 5
        assert parse(r)["ok"] is False

    def test_done_move_requires_verification_gate(self, company):
        r = run_state("ticket-create", "--dept", "backend", "--type", "bug",
                      "--priority", "low", "--title", "t2", "--desc", "d",
                      company_dir=company)
        tid = parse(r)["ticket_id"]
        env = {"HIVE_SCRIPTED_MOVE": "1"}
        for st in ("sprint-current", "in-progress", "in-review"):
            r = run_state("ticket-move", "--dept", "backend", "--ticket", tid,
                          "--status", st, company_dir=company, env_extra=env)
            assert r.returncode == 0, r.stderr
        # no gates_passed / verified_by → done is denied even scripted? No:
        # scripted moves bypass the gate (system safety valves). Use an
        # unscripted VP-style actor instead.
        r = run_state("ticket-move", "--dept", "backend", "--ticket", tid,
                      "--status", "done", "--reason", "implemented",
                      company_dir=company, env_extra={"HIVE_ROLE": "Verification-IC"})
        assert r.returncode == 3, r.stderr
        assert "gates" in parse(r)["error"].lower()

    def test_concurrent_ticket_moves_keep_all_comments(self, company):
        """Two racing writers (move + comment) on one ticket: the file
        must end valid with BOTH side effects — no lost update."""
        r = run_state("ticket-create", "--dept", "backend", "--type", "bug",
                      "--priority", "low", "--title", "race", "--desc", "d",
                      company_dir=company)
        tid = parse(r)["ticket_id"]
        env = {"HIVE_SCRIPTED_MOVE": "1"}
        run_state("ticket-move", "--dept", "backend", "--ticket", tid,
                  "--status", "sprint-current", company_dir=company, env_extra=env)

        def writer():
            return run_state("ticket-comment", "--dept", "backend", "--ticket", tid,
                             "--author", "A", "--text", "racing comment",
                             company_dir=company)

        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
            futs = [ex.submit(writer) for _ in range(4)]
            for f in futs:
                assert f.result().returncode == 0

        tfile = company / "departments" / "backend" / "tickets" / "sprint-current" / f"{tid}.json"
        t = json.loads(tfile.read_text())  # valid JSON, not torn
        bodies = [c["body"] for c in t["comments"]]
        assert bodies.count("racing comment") == 4



class TestSharedMoveAccessor:
    """G2: one mutation API. The lifecycle's scripted move and the MCP
    tool's move must reach the SAME code path — ticket-move.sh over the
    G1 state layer — and a move made by each path must be
    indistinguishable in the ticket file it produces."""

    def _company_with_backlog_ticket(self, tmp_path, label):
        company = tmp_path / label
        tdir = company / "departments" / "backend" / "tickets"
        (tdir / "backlog").mkdir(parents=True)
        (tdir / "in-progress").mkdir()
        (company / "counters").mkdir()
        (company / "counters" / "BACKEND.json").write_text('{"next_id": 2}')
        tid = "BACKEND-0001"
        (tdir / "backlog" / f"{tid}.json").write_text(json.dumps({
            "id": tid, "title": "t", "type": "feature", "priority": "high",
            "status": "backlog", "dept": "backend", "assignee": "",
            "bounce_count": 0, "comments": [],
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }))
        return company, tid

    @staticmethod
    def _ticket_state(company, tid):
        for d in ("backlog", "sprint-current", "in-progress", "in-review",
                  "done", "archive"):
            f = company / "departments" / "backend" / "tickets" / d / f"{tid}.json"
            if f.is_file():
                data = json.loads(f.read_text())
                data.pop("updated_at", None)
                return d, data
        return None, None

    def test_both_paths_name_the_same_move_script(self):
        """Static guard: the lifecycle's scripted_move and the MCP
        ticket_move tool must both route through ticket-move.sh."""
        life = (LIB / "tools" / "sprint-lifecycle.sh").read_text()
        assert 'ticket-move.sh' in life, \
            "lifecycle no longer calls ticket-move.sh (G2: one mutation API)"
        mcp = (LIB / "hive_mcp" / "tools" / "tickets.py").read_text()
        assert "ticket-move" in mcp, \
            "MCP ticket tools no longer route through ticket-move.sh"

    def test_lifecycle_and_mcp_moves_are_indistinguishable(self, tmp_path):
        # Same ticket, same move, two caller identities: the MCP tool's
        # role-based path and the lifecycle's scripted path.
        a, tid = self._company_with_backlog_ticket(tmp_path, "a")
        env_mcp = {**os.environ, "COMPANY_DIR": str(a),
                   "HIVE_ROLE": "Verification-IC", "HIVE_AGENT": "verifier"}
        r = subprocess.run(
            [str(LIB / "tools" / "ticket-move.sh"), "backend", tid,
             "in-progress", "shared move", "sprint-lifecycle"],
            capture_output=True, text=True, timeout=30, env=env_mcp)
        assert r.returncode == 0, r.stderr
        dir_a, state_a = self._ticket_state(a, tid)
        assert dir_a == "in-progress"

        b, tid2 = self._company_with_backlog_ticket(tmp_path, "b")
        env_lifecycle = {**os.environ, "COMPANY_DIR": str(b),
                         "HIVE_SCRIPTED_MOVE": "1", "HIVE_ROLE": "System",
                         "HIVE_AGENT": "sprint-lifecycle"}
        r = subprocess.run(
            [str(LIB / "tools" / "ticket-move.sh"), "backend", tid2,
             "in-progress", "shared move", "sprint-lifecycle"],
            capture_output=True, text=True, timeout=30, env=env_lifecycle)
        assert r.returncode == 0, r.stderr
        dir_b, state_b = self._ticket_state(b, tid2)
        assert dir_b == "in-progress"

        assert dir_a == dir_b, "paths landed the ticket in different dirs"
        assert state_a == state_b, (
            "shared accessor drift: the two move paths produced different "
            f"ticket files: {state_a!r} vs {state_b!r}")



# ── round activity log ─────────────────────────────────────────────

class TestRoundActivityLog:
    def test_round_entry_race_loses_no_entry_one_schema(self, company):
        """G1 concurrency test: lifecycle round-entry racing on the same
        round loses no entry and produces one schema."""
        import concurrent.futures

        def append(i):
            return run_state("round-append", "--stage", "tdd-red",
                             "--agent", f"ic-{i}", "--dept", "backend",
                             "--role", "IC", "--ticket", f"BACKEND-{i:04d}",
                             "--status", "running", company_dir=company)

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            futs = [ex.submit(append, i) for i in range(8)]
            results = [f.result() for f in futs]
        assert all(r.returncode == 0 for r in results), [r.stderr for r in results]

        data = json.loads((company / "rounds" / "current-round.json").read_text())
        assert data["source"] == "sprint-lifecycle"
        assert len(data["entries"]) == 8  # no lost updates
        ids = [e["id"] for e in data["entries"]]
        assert len(set(ids)) == 8
        required = {"id", "agent", "dept", "role", "kind", "ticket",
                    "status", "queued_at", "started_at"}
        for e in data["entries"]:
            assert required <= set(e), f"entry missing fields: {e}"

    def test_completion_flips_running_entry_in_place(self, company):
        run_state("round-append", "--stage", "verify", "--agent", "v", "--dept", "backend",
                  "--role", "Verification-IC", "--ticket", "BACKEND-0001",
                  "--status", "running", company_dir=company)
        r = run_state("round-append", "--stage", "verify", "--agent", "v", "--dept", "backend",
                      "--role", "Verification-IC", "--ticket", "BACKEND-0001",
                      "--status", "done", company_dir=company)
        assert r.returncode == 0
        data = json.loads((company / "rounds" / "current-round.json").read_text())
        assert len(data["entries"]) == 1
        assert data["entries"][0]["status"] == "done"
        assert data["entries"][0]["ended_at"]


# ── write-path layering (grep test) ────────────────────────────────

# Patterns that identify a DIRECT write of a .company/ JSON file from a
# shell script (or an embedded python heredoc). After G1, every such
# mutation goes through lib/hive_state, so these must not appear.
WRITE_IDIOMS = [
    ("python os.replace", re.compile(r"os\.replace\(")),
    ("python json.dump to file", re.compile(r"json\.dump\((?![^\n]*sys\.stdout)")),
    ("tmp file creation", re.compile(r"(?:mv\s|>\s).{0,80}\.tmp\b|\b\.tmp\b.{0,80}(?:\s+mv\b|>)")),
    ("mktemp of a json file", re.compile(r"mktemp[^\n]*\.json")),
    ("redirect into a .json path", re.compile(r">\s*\"[^\"]*\.json\"")),
    ("append into a .json path", re.compile(r">>\s*\"[^\"]*\.json\"")),
    ("append into a history (jsonl) file", re.compile(r">>\s*\"\$[A-Za-z_]*[Hh]istory")),
]




# Project-local scaffolding (NOT .company/ state): init-finance-scaffold
# writes $PROJECT_ROOT/finance/*.json — finance department data that
# lives with the project, outside the company workspace. Out of G1's scope.
PROJECT_LOCAL_EXEMPT = {
    "lib/tools/init-finance-scaffold.sh",
}


class TestCompanyWriteLayering:
    def _sh_files(self):
        return sorted(LIB.rglob("*.sh"))

    def test_no_bash_json_writes_outside_state_layer(self):
        """G1 done-when #1: no bash script or python heredoc in lib/ writes
        a .company/ JSON file outside the state layer."""
        hits = []
        for f in self._sh_files():
            rel = f.relative_to(REPO_ROOT).as_posix()
            if rel in PROJECT_LOCAL_EXEMPT:
                continue
            for lineno, line in enumerate(f.read_text(errors="replace").splitlines(), 1):
                if line.lstrip().startswith("#"):
                    continue
                for name, rx in WRITE_IDIOMS:
                    if rx.search(line):
                        hits.append(f"{f.relative_to(REPO_ROOT)}:{lineno}: {name}: {line.strip()}")
        assert not hits, "direct .company/ JSON writes found (must go through lib/hive_state):\n" + "\n".join(hits)

    def test_ticket_scripts_route_through_state_layer(self):
        """G1 done-when #2: the central ticket mutation scripts are thin
        callers of the state layer."""
        for name in ("ticket-move.sh", "ticket-create.sh", "ticket-approve.sh",
                     "ticket-bounce.sh", "ticket-claim.sh", "ticket-comment.sh"):
            text = (LIB / "tools" / name).read_text()
            assert "hive-state.sh" in text or "hive_state" in text, \
                f"{name} does not call the state layer"
            assert "json.dump(" not in text, f"{name} still writes JSON directly"
            assert "os.replace(" not in text, f"{name} still writes JSON directly"

    def test_lifecycle_round_writes_route_through_state_layer(self, company):
        """The lifecycle's round writes are the activity-log accessor: a
        round file produced by `hs round-append` is the only writer output
        (G3 will retire the competing writers)."""
        r = run_state("round-append", "--stage", "plan-ceo", "--agent", "ceo",
                      "--dept", "executive", "--role", "CEO", "--ticket", "",
                      "--status", "running", company_dir=company)
        assert r.returncode == 0
        data = json.loads((company / "rounds" / "current-round.json").read_text())
        assert data["source"] == "sprint-lifecycle"
        e = data["entries"][-1]
        assert e["kind"] == "plan-ceo" and e["status"] == "running"
