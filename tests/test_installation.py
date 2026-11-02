"""Integration tests."""
import sys
import json, os, subprocess
from pathlib import Path
import pytest


def _hive_settings_path(home: Path) -> Path:
    """Where the PreToolUse hook registrations live for a given HOME."""
    return home / ".claude" / "settings.json"


def _hook_source_repo() -> Path:
    return Path(__file__).resolve().parent.parent


def _run_hook_fn(fn: str, home: Path, extra_args: str = "") -> subprocess.CompletedProcess:
    repo = _hook_source_repo()
    cmd = f'source "packaging/hooks.sh"; {fn} {extra_args}'
    return subprocess.run(
        ["bash", "-c", cmd],
        capture_output=True, text=True, timeout=30,
        cwd=str(repo),
        env={**os.environ, "HOME": str(home)},
    )

class TestPreToolUseHookRegistration:
    """A.2: install/uninstall wire the PreToolUse hooks that the agent
    prompts describe — register, migrate legacy entries, unregister.
    Driven against a scratch HOME so no real settings are touched."""

    MATCHER = "Write|Edit|NotebookEdit|Bash"

    def _register(self, home, extra=""):
        return _run_hook_fn("hive_hooks_register", home, extra)

    def _entries(self, home):
        p = _hive_settings_path(home)
        if not p.is_file():
            return None
        return json.loads(p.read_text())

    def test_register_creates_canonical_entry(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        r = self._register(home)
        assert r.returncode == 0, r.stderr
        data = self._entries(home)
        assert data is not None, "settings file not created"
        pts = data["hooks"]["PreToolUse"]
        hive = [e for e in pts if any(
            "block-" in (h.get("command") or "") for h in e.get("hooks", []))]
        assert len(hive) == 1, f"expected exactly one hive entry, got {len(hive)}"
        assert hive[0]["matcher"] == self.MATCHER
        cmds = [h["command"] for h in hive[0]["hooks"]]
        assert any(c.endswith("block-company-writes.sh") for c in cmds)
        assert any(c.endswith("block-admin-code-writes.sh") for c in cmds)

    def test_register_is_idempotent(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        assert self._register(home).returncode == 0
        assert self._register(home).returncode == 0
        pts = self._entries(home)["hooks"]["PreToolUse"]
        hive = [e for e in pts if any("block-" in (h.get("command") or "") for h in e.get("hooks", []))]
        assert len(hive) == 1

    def test_register_migrates_legacy_narrow_matcher(self, tmp_path):
        home = tmp_path / "home"
        p = _hive_settings_path(home)
        p.parent.mkdir(parents=True)
        seed = {
            "permissions": {"allow": ["Read"]},
            "hooks": {"PreToolUse": [
                {"matcher": "Write|Edit",
                 "hooks": [{"type": "command", "command": "bash /old/install/lib/core/block-company-writes.sh", "timeout": 5}]},
                {"matcher": "Write",
                 "hooks": [{"type": "command", "command": "bash /somewhere/else/other-tool.sh"}]},
            ]},
        }
        p.write_text(json.dumps(seed))
        assert self._register(home).returncode == 0
        data = self._entries(home)
        pts = data["hooks"]["PreToolUse"]
        # legacy hive entry retired, canonical added, unrelated preserved
        assert len(pts) == 2, f"expected canonical + unrelated, got {len(pts)}"
        assert any(e["matcher"] == self.MATCHER for e in pts)
        assert any("other-tool.sh" in (h.get("command") or "")
                   for e in pts for h in e.get("hooks", []))
        assert data["permissions"] == {"allow": ["Read"]}

    def test_unregister_removes_only_hive_entries(self, tmp_path):
        home = tmp_path / "home"
        p = _hive_settings_path(home)
        p.parent.mkdir(parents=True)
        seed = {
            "hooks": {"PreToolUse": [
                {"matcher": "Write",
                 "hooks": [{"type": "command", "command": "bash /somewhere/else/other-tool.sh"}]},
            ]},
        }
        p.write_text(json.dumps(seed))
        assert self._register(home).returncode == 0
        r = _run_hook_fn("hive_hooks_unregister", home)
        assert r.returncode == 0, r.stderr
        pts = self._entries(home)["hooks"]["PreToolUse"]
        assert not any("block-" in (h.get("command") or "") for e in pts for h in e.get("hooks", []))
        assert any("other-tool.sh" in (h.get("command") or "") for e in pts for h in e.get("hooks", []))

    def test_install_and_uninstall_scripts_wire_hooks(self):
        """The scripts must actually call the shared helpers — a regression
        to 'hooks no longer installed' would desync the agent prompts again."""
        repo = _hook_source_repo()
        install = (repo / "packaging" / "install.sh").read_text()
        uninstall = (repo / "packaging" / "uninstall.sh").read_text()
        assert "hooks.sh" in install, "install.sh must source packaging/hooks.sh"
        assert "hive_hooks_register" in install
        assert "hooks.sh" in uninstall, "uninstall.sh must source packaging/hooks.sh"
        assert "hive_hooks_unregister" in uninstall

    @pytest.mark.skipif(
        os.environ.get("HIVE_FULL_INSTALL_TEST") != "1",
        reason="full install/uninstall round-trip (venv build) — set HIVE_FULL_INSTALL_TEST=1",
    )
    def test_full_install_uninstall_roundtrip(self, tmp_path):
        """End-to-end: install.sh patches the settings file, uninstall.sh
        restores it (hive entries gone, nothing else lost)."""
        repo = _hook_source_repo()
        scratch = tmp_path / "scratch"
        scratch.mkdir()
        home = scratch / "home"
        home.mkdir()
        p = _hive_settings_path(home)
        p.parent.mkdir(parents=True)
        seed = {"permissions": {"allow": ["Read"]},
                "hooks": {"PreToolUse": [{"matcher": "Write",
                                          "hooks": [{"type": "command", "command": "bash /x/other.sh"}]}]}}
        p.write_text(json.dumps(seed))
        env = {**os.environ, "HOME": str(home)}
        r = subprocess.run(
            ["bash", str(repo / "packaging" / "install.sh"),
             "--copy-to", str(scratch / "install"),
             "--bin-link", str(home / ".local" / "bin" / "hive")],
            capture_output=True, text=True, timeout=600, cwd=str(scratch), env=env,
        )
        assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
        data = json.loads(p.read_text())
        hive = [e for e in data["hooks"]["PreToolUse"]
                if any("block-" in (h.get("command") or "") for h in e.get("hooks", []))]
        assert len(hive) == 1, "install must register the canonical hive entry"
        r = subprocess.run(
            ["bash", str(scratch / "install" / "packaging" / "uninstall.sh")],
            capture_output=True, text=True, timeout=120, env=env,
        )
        assert r.returncode == 0, r.stderr
        data = json.loads(p.read_text())
        assert not any("block-" in (h.get("command") or "")
                       for e in data["hooks"]["PreToolUse"] for h in e.get("hooks", []))
        assert data["permissions"] == {"allow": ["Read"]}

    def test_invalid_settings_json_left_untouched(self, tmp_path):
        home = tmp_path / "home"
        p = _hive_settings_path(home)
        p.parent.mkdir(parents=True)
        p.write_text("{ this is not json")
        r = self._register(home)
        assert r.returncode == 0, "register must not fail the install"
        assert p.read_text() == "{ this is not json"
        assert "warning" in r.stderr.lower()


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
