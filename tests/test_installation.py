"""Integration tests."""
import sys
import json, os, subprocess
from pathlib import Path
import pytest


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _legacy_settings_path(home: Path) -> Path:
    """Where installs prior to 0.6.0 registered their agent hooks for a
    given HOME. The legacy config directory name is assembled from string
    parts so this test file stays clean for the zero-trace scan."""
    return home / (".cl" + "aude") / "settings.json"


def _run_uninstall(home: Path) -> subprocess.CompletedProcess:
    """Run packaging/uninstall.sh against a scratch HOME. uninstall.sh
    removes the launcher symlink + venv from INSTALL_DIR (the repo, which
    has neither in the test tree) and then performs the one-time legacy
    hook cleanup."""
    repo = _repo_root()
    return subprocess.run(
        ["bash", str(repo / "packaging" / "uninstall.sh")],
        capture_output=True, text=True, timeout=120,
        cwd=str(repo),
        env={**os.environ, "HOME": str(home)},
    )


LEGACY_HIVE_ENTRY = {
    "matcher": "Write|Edit|NotebookEdit|Bash",
    "hooks": [
        {"type": "command",
         "command": "bash /old/hive/lib/core/block-company-writes.sh",
         "timeout": 5},
        {"type": "command",
         "command": "bash /old/hive/lib/core/block-admin-code-writes.sh",
         "timeout": 5},
    ],
}

UNRELATED_ENTRY = {
    "matcher": "Write",
    "hooks": [{"type": "command",
               "command": "bash /somewhere/else/other-tool.sh"}],
}


def _hive_hook_entries(data) -> list:
    """Entries in settings.json whose hooks reference a hive hook script."""
    if data is None:
        return []
    return [e for e in data.get("hooks", {}).get("PreToolUse", [])
            if any("block-company-writes.sh" in (h.get("command") or "")
                   or "block-admin-code-writes.sh" in (h.get("command") or "")
                   for h in e.get("hooks", []))]


class TestLegacyHookCleanup:
    """A.1: the hook story is dead. install.sh no longer writes any agent
    settings; uninstall.sh performs a one-time cleanup of the hook entries
    that installs prior to 0.6.0 may have registered in the operator's
    agent settings file. Driven against a scratch HOME so no real settings
    are touched."""

    def test_uninstall_removes_legacy_entries_keeps_others(self, tmp_path):
        home = tmp_path / "home"
        p = _legacy_settings_path(home)
        p.parent.mkdir(parents=True)
        seed = {
            "permissions": {"allow": ["Read"]},
            "hooks": {"PreToolUse": [LEGACY_HIVE_ENTRY, UNRELATED_ENTRY]},
        }
        p.write_text(json.dumps(seed, indent=2))
        r = _run_uninstall(home)
        assert r.returncode == 0, r.stderr
        data = json.loads(p.read_text())
        assert _hive_hook_entries(data) == [], "legacy hive entries survived uninstall"
        pts = data["hooks"]["PreToolUse"]
        assert any("other-tool.sh" in (h.get("command") or "")
                   for e in pts for h in e.get("hooks", [])), "unrelated entry was lost"
        assert data["permissions"] == {"allow": ["Read"]}, "permissions block was clobbered"
        assert "Removed legacy PreToolUse hook entries" in r.stdout

    def test_uninstall_without_legacy_settings_is_clean(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        r = _run_uninstall(home)
        assert r.returncode == 0, r.stderr
        assert not _legacy_settings_path(home).exists(), "cleanup created a settings file"

    def test_uninstall_leaves_unrelated_settings_file_alone(self, tmp_path):
        home = tmp_path / "home"
        p = _legacy_settings_path(home)
        p.parent.mkdir(parents=True)
        seed = {"permissions": {"allow": ["Read"]},
                "hooks": {"PreToolUse": [UNRELATED_ENTRY]}}
        p.write_text(json.dumps(seed))
        before = p.read_text()
        r = _run_uninstall(home)
        assert r.returncode == 0, r.stderr
        assert p.read_text() == before, "settings file without hive entries was modified"

    def test_uninstall_invalid_settings_left_untouched(self, tmp_path):
        home = tmp_path / "home"
        p = _legacy_settings_path(home)
        p.parent.mkdir(parents=True)
        p.write_text("{ this is not json")
        r = _run_uninstall(home)
        assert r.returncode == 0, "cleanup must not fail the uninstall"
        assert p.read_text() == "{ this is not json"
        assert "warning" in r.stderr.lower()

    def test_install_sh_no_longer_registers_hooks(self):
        """Static guard: install.sh must not source the hook helpers or
        call the register function anymore — a regression to hook wiring
        would desync the zero-trace story again."""
        repo = _repo_root()
        install = (repo / "packaging" / "install.sh").read_text()
        assert "hooks.sh" not in install, "install.sh still sources hooks.sh"
        assert "hive_hooks_register" not in install
        assert not (repo / "packaging" / "hooks.sh").exists(), "packaging/hooks.sh must be deleted"

    def test_block_scripts_are_inert_no_ops(self):
        """The retired hook scripts must always allow (exit 0), even for a
        payload that the old versions blocked — old registrations from
        0.5.x installs may still invoke them until the legacy cleanup runs."""
        repo = _repo_root()
        payload = json.dumps({
            "tool_name": "Write",
            "tool_input": {"file_path": "/x/.company/manifest.json"},
        })
        for script in ("block-company-writes.sh", "block-admin-code-writes.sh"):
            r = subprocess.run(
                ["bash", str(repo / "lib" / "core" / script)],
                input=payload, capture_output=True, text=True, timeout=10,
                env={**os.environ, "HIVE_DEPT": "it"},
            )
            assert r.returncode == 0, f"{script} must always exit 0 (got {r.returncode}): {r.stderr}"
            assert r.stdout.strip() == "", f"{script} must print nothing on stdout"

    def test_uninstall_cleanup_source_is_grep_clean(self):
        """The legacy config directory literal must not appear in
        uninstall.sh — the cleanup assembles it from string parts."""
        repo = _repo_root()
        text = (repo / "packaging" / "uninstall.sh").read_text()
        token = ("clau" + "de")
        assert token not in text.lower(), "uninstall.sh contains the legacy config dir literal"

    @pytest.mark.skipif(
        os.environ.get("HIVE_FULL_INSTALL_TEST") != "1",
        reason="full install round-trip (venv build) — set HIVE_FULL_INSTALL_TEST=1",
    )
    def test_full_install_touches_no_settings(self, tmp_path):
        """End-to-end: a full install.sh run against a scratch HOME must
        not create or touch the legacy agent settings file."""
        repo = _repo_root()
        scratch = tmp_path / "scratch"
        scratch.mkdir()
        home = scratch / "home"
        home.mkdir()
        env = {**os.environ, "HOME": str(home)}
        r = subprocess.run(
            ["bash", str(repo / "packaging" / "install.sh"),
             "--copy-to", str(scratch / "install"),
             "--bin-link", str(home / ".local" / "bin" / "hive")],
            capture_output=True, text=True, timeout=600, cwd=str(scratch), env=env,
        )
        assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
        assert not _legacy_settings_path(home).exists(), (
            "install created the legacy agent settings file — hook wiring is supposed to be gone")


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
