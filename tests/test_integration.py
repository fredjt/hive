"""Integration tests."""
import json, os, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))  # for stub_llm
from stub_llm import StubLLM  # noqa: E402

class TestFullInitFlow:
    def test_project_dir_created(self, tmp_project_dir):
        """Project directory should exist."""
        assert tmp_project_dir.is_dir()

    def test_installed_scripts_accessible(self, tmp_project_dir):
        """Installed scripts should be accessible via HIVE_ROOT."""
        install_dir = Path(__file__).resolve().parent.parent
        assert (install_dir / "lib" / "tools" / "ticket-create.sh").exists()
        assert (install_dir / "lib" / "agents" / "ceo.md").exists()
        assert (install_dir / "lib" / "core" / "init.sh").exists()

class TestTicketLifecycle:
    def test_ticket_create_moves_to_backlog(self, tmp_company_dir):
        install_dir = Path(__file__).resolve().parent.parent
        result = subprocess.run(
            ["bash", str(install_dir / "lib" / "tools" / "ticket-create.sh"), "it", "feature", "medium", "Test Ticket", "A test"],
            capture_output=True, text=True, timeout=30,
            cwd=str(tmp_company_dir.parent),
            env={**os.environ, "HIVE_INSTALL_DIR": str(install_dir), "COMPANY_DIR": str(tmp_company_dir)},
        )
        assert result.returncode == 0
        tid = result.stdout.strip()
        bf = tmp_company_dir / "departments" / "it" / "tickets" / "backlog" / f"{tid}.json"
        assert bf.exists(), f"Ticket not found in backlog: {tid}"

    def test_ticket_json_is_valid(self, tmp_company_dir):
        install_dir = Path(__file__).resolve().parent.parent
        result = subprocess.run(
            ["bash", str(install_dir / "lib" / "tools" / "ticket-create.sh"), "it", "feature", "medium", "Test Ticket 2", "A test"],
            capture_output=True, text=True, timeout=30,
            cwd=str(tmp_company_dir.parent),
            env={**os.environ, "HIVE_INSTALL_DIR": str(install_dir), "COMPANY_DIR": str(tmp_company_dir)},
        )
        if result.returncode != 0:
            pytest.skip("Ticket creation failed")
        tid = result.stdout.strip()
        bf = tmp_company_dir / "departments" / "it" / "tickets" / "backlog" / f"{tid}.json"
        if not bf.exists():
            pytest.skip("Ticket file not found")
        data = json.loads(bf.read_text())
        assert "id" in data and "title" in data and "status" in data

class TestTicketWorkKind:
    """W1 (0.23): the ticket carries the work kind.

    work_kind (code|deliverable) is a property of the GOAL, not of a
    pass: it is set at creation (validated against WORK_KINDS), persists
    across every status move, and is NOT cleared at done (contrast
    skip_tdd_red, which ticket-move clears — TestTicketMoveScript in
    test_bash_scripts.py pins that side). No write path mutates it after
    creation: a wrong kind is a new ticket, not an edit.
    """

    REPO = Path(__file__).resolve().parent.parent

    def _with_hr(self, company):
        # The fixture's org chart carries only it/rd; the tests file into
        # hr (a fixed dept of the legacy admin list — the deliverable-ish
        # half of the division), so add it.
        chart = json.loads((company / "org-chart.json").read_text())
        chart.setdefault("departments", {})["hr"] = {"name": "HR",
                                                     "reporting_to": None}
        (company / "org-chart.json").write_text(
            json.dumps(chart, indent=2))

    def _create(self, company, args):
        env = {**os.environ, "HIVE_INSTALL_DIR": str(self.REPO),
               "COMPANY_DIR": str(company)}
        return subprocess.run(
            ["bash", str(self.REPO / "lib" / "tools" / "ticket-create.sh")] + args,
            capture_output=True, text=True, timeout=30,
            cwd=str(company.parent), env=env)

    def _ticket_file(self, company, dept, status, tid):
        return (company / "departments" / dept / "tickets" / status /
                f"{tid}.json")

    def test_create_with_work_kind_writes_field(self, tmp_company_dir):
        self._with_hr(tmp_company_dir)
        r = self._create(tmp_company_dir,
                         ["hr", "chore", "medium", "Memo", "d",
                          "test", "manual", "--work-kind", "deliverable"])
        assert r.returncode == 0, r.stderr
        tid = r.stdout.strip()
        data = json.loads(self._ticket_file(tmp_company_dir, "hr",
                                            "backlog", tid).read_text())
        assert data.get("work_kind") == "deliverable"

    def test_create_without_work_kind_adds_no_key(self, tmp_company_dir):
        self._with_hr(tmp_company_dir)
        r = self._create(tmp_company_dir,
                         ["hr", "chore", "medium", "Memo", "d",
                          "test", "manual"])
        assert r.returncode == 0, r.stderr
        tid = r.stdout.strip()
        data = json.loads(self._ticket_file(tmp_company_dir, "hr",
                                            "backlog", tid).read_text())
        assert "work_kind" not in data, \
            "a filing without work_kind must be byte-identical to 0.22 (no key)"

    def test_create_with_bogus_work_kind_is_rejected(self, tmp_company_dir):
        self._with_hr(tmp_company_dir)
        r = self._create(tmp_company_dir,
                         ["hr", "chore", "medium", "Memo", "d",
                          "test", "manual", "--work-kind", "bogus"])
        assert r.returncode != 0
        assert "work_kind" in r.stderr, \
            "the rejection must name the work_kind field"
        backlog = tmp_company_dir / "departments" / "hr" / "tickets" / "backlog"
        assert not list(backlog.glob("*.json")), \
            "a rejected filing must not create a ticket"

    def test_work_kind_survives_the_full_move_sequence(self, tmp_company_dir):
        """backlog → sprint-current → in-progress → in-review → done:
        the field rides along and is NOT cleared at done."""
        self._with_hr(tmp_company_dir)
        r = self._create(tmp_company_dir,
                         ["hr", "chore", "medium", "Memo", "d",
                          "test", "manual", "--work-kind", "deliverable"])
        assert r.returncode == 0, r.stderr
        tid = r.stdout.strip()
        env = {**os.environ, "HIVE_INSTALL_DIR": str(self.REPO),
               "COMPANY_DIR": str(tmp_company_dir), "HIVE_SCRIPTED_MOVE": "1"}
        for status in ("sprint-current", "in-progress", "in-review"):
            mv = subprocess.run(
                ["bash", str(self.REPO / "lib" / "tools" / "ticket-move.sh"),
                 "hr", tid, status, "lifecycle move", "sprint-lifecycle"],
                capture_output=True, text=True, timeout=30, env=env)
            assert mv.returncode == 0, f"move to {status}: {mv.stderr}"
            data = json.loads(self._ticket_file(tmp_company_dir, "hr",
                                                status, tid).read_text())
            assert data.get("work_kind") == "deliverable", \
                f"work_kind lost at {status}"
        mv = subprocess.run(
            ["bash", str(self.REPO / "lib" / "tools" / "ticket-move.sh"),
             "hr", tid, "done", "shipped", "sprint-lifecycle", "implemented"],
            capture_output=True, text=True, timeout=30, env=env)
        assert mv.returncode == 0, mv.stderr
        data = json.loads(self._ticket_file(tmp_company_dir, "hr",
                                            "done", tid).read_text())
        assert data.get("work_kind") == "deliverable", \
            "work_kind was cleared at done (it is a property of the goal)"


class TestSprintStateLifecycle:
    def test_sprint_state_init(self, tmp_company_dir):
        install_dir = Path(__file__).resolve().parent.parent
        code = f'''source "{install_dir}/lib/tools/sprint-state.sh"
export COMPANY_DIR="{tmp_company_dir}"
ss_init
jq -r .stage "{tmp_company_dir}/sprints/current.json"'''
        result = subprocess.run(["bash", "-c", code], capture_output=True, text=True, timeout=10,
            env={**os.environ, "HIVE_INSTALL_DIR": str(install_dir)})
        assert result.returncode == 0 and "ceo-plan" in result.stdout, f"rc={result.returncode} stdout={result.stdout!r} stderr={result.stderr!r}"

class TestShellModuleIntegration:
    def test_shell_run_ticket_create(self, tmp_company_dir):
        from hive_mcp.shell import run
        result = run("ticket-create", "it", "feature", "medium", "Integration Test Ticket", "A test description",
                     company_dir=str(tmp_company_dir))
        assert isinstance(result, type(run("nonexistent-script-xyz")))

class TestSprintState:
    def test_sprint_state_functions_defined(self):
        code = 'source "lib/tools/sprint-state.sh" 2>/dev/null\nfor func in ss_init ss_get_stage ss_set_stage ss_mark_enqueued ss_is_enqueued ss_inc_loop_iteration ss_reset_loop_iteration ss_inc_release_retry ss_set_release_field ss_set_ceo_direction_path ss_set_tickets_in_sprint ss_active_ticket_count ss_unverified_in_review_tickets ss_archive_and_start_new; do\n    type "$func" >/dev/null 2>&1 && echo "FOUND:$func" || echo "MISSING:$func"\ndone'
        result = subprocess.run(["bash", "-c", code], capture_output=True, text=True, timeout=10,
            env={**os.environ, "HIVE_INSTALL_DIR": str(Path(__file__).resolve().parent.parent)})
        assert result.returncode == 0
        missing = [l for l in result.stdout.split("\n") if "MISSING" in l]
        assert len(missing) == 0, f"Missing functions: {missing}"

class TestLLMUsageCost:
    """B1 (0.9.0): per-call cost in the usage log.

    The harness records cost_usd on every LLM call (computed by
    llm_providers from the provider's usage block; 0.0 for providers
    that report no cost). Readers (hive llm stats) must keep parsing
    entries WITHOUT the field (written by pre-0.9.0 harnesses) and
    treat their cost as 0 — never a parse error.
    """

    REPO = Path(__file__).resolve().parent.parent

    def test_harness_call_logs_cost(self, tmp_path):
        stub = StubLLM()
        try:
            company = tmp_path / "company"
            (company / "logs").mkdir(parents=True)
            r = subprocess.run(
                [sys.executable, str(self.REPO / "lib" / "core" / "agent-harness.py"),
                 "--api-base", stub.base_url, "--api-key", "sk-test",
                 "--model", "gpt-4o", "--", "Hello"],
                capture_output=True, text=True, timeout=60,
                cwd=str(tmp_path),
                env={**os.environ,
                     "COMPANY_DIR": str(company),
                     "HIVE_CONFIG": str(tmp_path / "no-such-config.toml")},
            )
            combined = r.stdout + r.stderr
            assert r.returncode == 0, combined
            log = company / "logs" / "llm-usage.log"
            assert log.exists(), combined
            entries = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
            assert len(entries) == 1, entries
            e = entries[0]
            assert e["tokens"] == 150
            # gpt-4o is priced on the gpt-4 branch: (100*3e-5 + 50*6e-5)/1000
            assert e["cost_usd"] == pytest.approx(6e-06), e
        finally:
            stub.stop()

    def test_stats_parses_entries_without_cost(self, tmp_path):
        """A legacy entry (no cost_usd field) must not break `hive llm stats`,
        and must count for 0 cost."""
        company = tmp_path / "company"
        (company / "logs").mkdir(parents=True)
        log = company / "logs" / "llm-usage.log"
        # Dynamic timestamp: stats counts a 24h window, so a hardcoded
        # date goes stale and the entries age out of the window (the
        # suite was green on the write date and red the day after).
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        log.write_text(
            json.dumps({"ts": now, "caller": "ic-engineer", "mode": "agent",
                        "model": "gpt-4o", "turn": 1, "tokens": 100,
                        "response": "legacy entry written by pre-0.9.0 harness"}) + "\n"
            + json.dumps({"ts": now, "caller": "ic-tester", "mode": "agent",
                          "model": "gpt-4o", "turn": 1, "tokens": 50,
                          "cost_usd": 0.000001, "response": "new entry with cost"}) + "\n"
        )
        r = subprocess.run(
            [sys.executable, "-m", "hive_cli.main", "llm", "stats"],
            capture_output=True, text=True, timeout=30,
            env={**os.environ, "PYTHONPATH": str(self.REPO / "lib"),
                 "COMPANY_DIR": str(company),
                 "HIVE_CONFIG": str(tmp_path / "no-such-config.toml")},
        )
        combined = r.stdout + r.stderr
        assert r.returncode == 0, combined
        assert "ic-engineer" in combined  # the cost-less entry was parsed


class TestICSpawnDiagnostics:
    def test_ic_spawn_diagnostics_runs(self, tmp_project_dir):
        install_dir = Path(__file__).resolve().parent.parent
        company_dir = tmp_project_dir / ".company"
        company_dir.mkdir()



class TestNoTomliInLib:
    """T4 (0.15) — the 3.10 surface is RETIRED: `import tomli` is gone
    from lib/ (the static successor of TestTomliSurfaceGuard, which
    pinned the surface's PRESENCE and retires with it).

    The 3.11 floor makes stdlib tomllib the only TOML parser in the
    runtime. Any new `import tomli` in lib/ re-opens a version-sensitive
    surface (a marker-conditional dependency + a per-version CI row) and
    fails this guard until it is deliberately justified.
    """

    def test_no_import_tomli_in_lib(self):
        import re
        from conftest import get_install_dir
        repo = get_install_dir()
        found = set()
        for py in (repo / "lib").rglob("*.py"):
            if "__pycache__" in py.parts:
                continue
            text = py.read_text()
            if re.search(r"^\s*(import tomli|from tomli import)",
                         text, re.M):
                found.add(str(py.relative_to(repo)))
        assert not found, (
            f"`import tomli` in lib/ — the 3.10 surface is retired "
            f"(0.15, T4): {sorted(found)}. Under the 3.11 floor, stdlib "
            f"tomllib is the only TOML parser.")
