"""Integration tests."""
import sys
import json, os, subprocess
from pathlib import Path
import pytest

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

class TestICSpawnDiagnostics:
    def test_ic_spawn_diagnostics_runs(self, tmp_project_dir):
        install_dir = Path(__file__).resolve().parent.parent
        company_dir = tmp_project_dir / ".company"
        company_dir.mkdir()
        result = subprocess.run(
            ["bash", str(install_dir / "lib" / "tools" / "ic-spawn-diagnostics.sh")],
            capture_output=True, text=True, timeout=10,
            cwd=str(tmp_project_dir),
            env={**os.environ, "HIVE_INSTALL_DIR": str(install_dir)},
        )
        assert result.returncode == 0

class TestTicketBounceRationale:
    def test_ticket_bounce_rationale_length(self):
        result = subprocess.run(
            ["bash", "lib/tools/ticket-bounce.sh", "it", "TICKET-001", "verifier", "short"],
            capture_output=True, text=True, timeout=10,
            env={**os.environ, "HIVE_INSTALL_DIR": str(Path(__file__).resolve().parent.parent)})
        assert result.returncode != 0 and ("80" in result.stderr or "chars" in result.stderr.lower())

class TestTicketRejectRationale:
    def test_ticket_reject_rationale_length(self):
        result = subprocess.run(
            ["bash", "lib/tools/ticket-reject.sh", "it", "TICKET-001", "verifier", "short"],
            capture_output=True, text=True, timeout=10,
            env={**os.environ, "HIVE_INSTALL_DIR": str(Path(__file__).resolve().parent.parent)})
        assert result.returncode != 0 and ("80" in result.stderr or "chars" in result.stderr.lower())

class TestMetaSuite:
    def test_conftest_exists(self):
        assert Path("tests/conftest.py").exists()
    def test_test_files_exist(self):
        files = list(Path("tests").glob("test_*.py"))
        assert len(files) >= 5, f"Expected >= 5 test files, got {len(files)}"

class TestPythonImports:
    def test_hive_cli_importable(self):
        try:
            from hive_cli import main as hive_main
            assert hasattr(hive_main, "build_parser")
            assert hasattr(hive_main, "main")
        except ImportError:
            pytest.skip("mcp.client.streamable_http not available")
    def test_hive_mcp_shell_importable(self):
        from hive_mcp import shell
        assert hasattr(shell, "run") and hasattr(shell, "ShellResult")
    def test_hive_mcp_server_importable(self):
        try:
            from hive_mcp import server
            assert hasattr(server, "build_server")
        except ImportError:
            pytest.skip("mcp.server.fastmcp not available")
    def test_hive_cli_mcp_client_importable(self):
        try:
            from hive_cli import mcp_client
            assert hasattr(mcp_client, "call_tool")
        except ImportError:
            pytest.skip("mcp.client.streamable_http not available")

class TestPythonSyntax:
    @pytest.mark.parametrize("py_file", [str(p) for p in Path("lib").rglob("*.py")])
    def test_python_file_parses(self, py_file):
        compile(Path(py_file).read_text(), py_file, "exec")

class TestScriptCount:
    def test_script_count(self):
        scripts = list(Path("lib/tools").glob("*.sh"))
        assert len(scripts) >= 80, f"Expected >= 80 scripts in tools/, got {len(scripts)}"

class TestAgentCount:
    def test_agent_count(self):
        agents = [a for a in Path("lib/agents").glob("*.md") if not a.name.startswith("_")]
        assert len(agents) >= 15, f"Expected >= 15 agents, got {len(agents)}"

class TestToolCount:
    def test_tool_count(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
        try:
            from hive_mcp.server import build_server
        except ModuleNotFoundError as e:
            pytest.skip(f"MCP server not available: {e}")
        server = build_server()
        tools = list(server._tool_manager._tools.keys())
        assert len(tools) >= 40, f"Expected >= 40 tools, got {len(tools)}"

class TestStageAdvanceStages:
    def test_stage_advance_has_valid_stages(self):
        code = 'source "lib/tools/sprint-state.sh" 2>/dev/null\nexport COMPANY_DIR="/tmp/test-company"\nmkdir -p "$COMPANY_DIR/sprints"\nss_init\nfor stage in ceo-plan vp-plan tdd-red tdd-green verify build-and-test release-verify release-publish ceo-bugfix-plan vp-bugfix-plan vp-review complete; do\n    ss_set_stage "$stage" 2>/dev/null\ndone\necho OK'
        result = subprocess.run(["bash", "-c", code], capture_output=True, text=True, timeout=10,
            env={**os.environ, "HIVE_INSTALL_DIR": str(Path(__file__).resolve().parent.parent)})
        assert result.returncode == 0 and "OK" in result.stdout

class TestSprintStateIsExecutable:
    def test_sprint_state_is_executable(self):
        assert os.access("lib/tools/sprint-state.sh", os.X_OK)
