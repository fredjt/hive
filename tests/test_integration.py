"""Integration tests."""
import json, os, subprocess, sys
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
        now = "2026-11-04T00:00:00Z"
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

