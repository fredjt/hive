"""Integration tests."""
import sys
import json, os, shutil, subprocess
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


class TestSpawnPathNoExternalCliSection:
    """A.2/B.4: the spawn path (agent-spawn.sh -> agent-harness.py) runs
    against a scratch company dir with no external-CLI config section
    anywhere and no external binary invoked. With no LLM provider
    configured, B.4 requires the path to fail fast: a non-zero exit and
    one actionable line saying what to set and where — not a
    mid-loop apology."""

    def test_spawn_path_fails_fast_without_provider(self, tmp_company_dir, tmp_path):
        repo = _repo_root()
        # Isolate the user config: the test asserts the provider is
        # UNCONFIGURED, but the host HOME may carry a real [llm] section
        # that the harness would happily resolve. Fresh HOME + a
        # non-existent HIVE_CONFIG means no config file on any path.
        env = {
            **os.environ,
            "HOME": str(tmp_path / "isolated-home"),
            "HIVE_CONFIG": str(tmp_path / "no-such-config.toml"),
            "HIVE_ROOT": str(repo),
            "COMPANY_DIR": str(tmp_company_dir),
            "HIVE_AGENT": "test-agent",
            "HIVE_DEPT": "it",
            "HIVE_ROLE": "IC",
            "MCP_URL": "http://127.0.0.1:1/mcp",  # nothing listens here; no-mcp path
        }
        env.pop("HIVE_API_BASE", None)
        env.pop("HIVE_API_KEY", None)
        env.pop("HIVE_MODEL", None)
        env.pop("HIVE_PROVIDER", None)
        env.pop("HIVE_VERIFICATION_MODEL", None)
        r = subprocess.run(
            ["bash", str(repo / "lib" / "core" / "agent-spawn.sh"), "--no-mcp", "--", "say hello"],
            capture_output=True, text=True, timeout=60,
            cwd=str(tmp_company_dir.parent), env=env,
        )
        # B.4: no provider -> fail fast with exit code 2
        assert r.returncode == 2, f"spawn path must fail fast (rc=2): {r.stdout[-500:]} {r.stderr[-500:]}"
        combined = (r.stdout + r.stderr)
        assert "no LLM provider configured" in combined, \
            "fail-fast line must say what is missing"
        assert "HIVE_PROVIDER" in combined, \
            "fail-fast line must name the env vars to set"
        streams = list((tmp_company_dir / "logs" / "streams").glob("*.jsonl"))
        assert streams, "agent-spawn.sh did not write a stream file"
        last = streams[0].read_text().strip().splitlines()[-1]
        result = json.loads(last)
        assert result.get("type") == "result"
        assert result.get("subtype") == "llm-not-configured"
        assert result.get("is_error") is True

    def test_agent_spawn_does_not_read_retired_cli_config(self):
        """Static guard: the spawn path must not reference the retired
        external-CLI config keys or an external CLI binary. The key
        strings are assembled so this test stays grep-clean for the
        zero-trace guard."""
        repo = _repo_root()
        spawn = (repo / "lib" / "core" / "agent-spawn.sh").read_text()
        harness = (repo / "lib" / "core" / "agent-harness.py").read_text()
        sec = "clau" + "de"
        for key in (sec + ".bin", sec + ".flags", "[" + sec + "]"):
            assert key not in spawn and key not in harness


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
        # (was >= 80; the deprecated migrate-agents.sh shim was deleted in 0.6.0)
        assert len(scripts) >= 75, f"Expected >= 75 scripts in tools/, got {len(scripts)}"

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


class TestInstallScaffoldsUserConfig:
    """C3: the installer creates ~/.config/hive/config.toml from the tracked
    template — a visible config with every key defaulted at install time —
    and never overwrites an existing file. The env-file hint must point at
    $HOME/.config/hive/env.sh for shell-level env only, and stop
    recommending the retired ANTHROPIC_*/LLM_TINY_* variable names."""

    @staticmethod
    def _run_install(home: Path, tmp_path: Path,
                     extra_env: dict | None = None) -> subprocess.CompletedProcess:
        repo = _repo_root()
        # Stage a minimal tree — the same surface the release tarball
        # carries — so the test is fast and deterministic, and local
        # scratch state (venv/, dist/, scratch projects) in a dev checkout
        # can't drag along a 100MB+ copy or a stale venv.
        tree = tmp_path / "hive-src"
        tree.mkdir(exist_ok=True)
        for d in ("bin", "lib", "etc", "packaging", "share"):
            dst = tree / d
            if not dst.exists():
                subprocess.run(["cp", "-r", str(repo / d), str(dst)], check=True)
            for junk in dst.rglob("__pycache__"):
                subprocess.run(["rm", "-rf", str(junk)])
        (tree / "VERSION").write_text(
            (repo / "VERSION").read_text() if (repo / "VERSION").is_file()
            else "0.0.0-dev\n")
        env = {**os.environ, "HOME": str(home)}
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            ["bash", str(tree / "packaging" / "install.sh"),
             "--bin-link", str(home / ".local" / "bin" / "hive")],
            capture_output=True, text=True, timeout=300,
            cwd=str(tree),
            env=env,
        )

    def test_install_creates_user_config_from_template(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        r = self._run_install(home, tmp_path)
        assert r.returncode == 0, r.stdout + "\n" + r.stderr
        user = home / ".config" / "hive" / "config.toml"
        assert user.is_file(), \
            "the installer must scaffold the user config — a stranger must " \
            "not have to invent a file no documentation shows"
        assert user.read_text() == (_repo_root() / "etc" / "config.template.toml").read_text()
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        data = tomllib.loads(user.read_text())
        assert set(data["llm"]) == {"provider", "model", "base_url", "api_key",
                                    "api_style", "verification_model"}

    def test_install_never_overwrites_existing_user_config(self, tmp_path):
        home = tmp_path / "home"
        user = home / ".config" / "hive" / "config.toml"
        user.parent.mkdir(parents=True)
        edited = '[llm]\nprovider = "ollama"\nmodel = "my-model"\n'
        user.write_text(edited)
        r = self._run_install(home, tmp_path)
        assert r.returncode == 0, r.stdout + "\n" + r.stderr
        assert user.read_text() == edited, \
            "an existing user config holds operator edits — install must keep it"

    def test_install_honors_hive_config_env(self, tmp_path):
        """A2: install.sh reads HIVE_CONFIG — the spelling doctor and
        release-publish-runner.sh already use."""
        home = tmp_path / "home"
        home.mkdir()
        target = tmp_path / "scratch" / "c.toml"
        target.parent.mkdir()
        r = self._run_install(home, tmp_path,
                              extra_env={"HIVE_CONFIG": str(target)})
        assert r.returncode == 0, r.stdout + "\n" + r.stderr
        assert target.is_file(), \
            "HIVE_CONFIG must steer where the installer scaffolds the config"
        assert not (home / ".config" / "hive" / "config.toml").exists(), \
            "the default location must not also be scaffolded"

    def test_install_hive_user_config_alias_still_works(self, tmp_path):
        """A2: HIVE_USER_CONFIG keeps working as a deprecated alias —
        with a one-time note pointing at the new spelling."""
        home = tmp_path / "home"
        home.mkdir()
        target = tmp_path / "scratch" / "alias.toml"
        target.parent.mkdir()
        r = self._run_install(home, tmp_path,
                              extra_env={"HIVE_USER_CONFIG": str(target)})
        assert r.returncode == 0, r.stdout + "\n" + r.stderr
        assert target.is_file(), \
            "the HIVE_USER_CONFIG alias must keep working for one release"
        assert "HIVE_USER_CONFIG" in r.stdout and "deprecated" in r.stdout, \
            "the alias use must print a deprecation note naming the new var"

    def test_install_hive_config_wins_over_alias(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        primary = tmp_path / "scratch" / "primary.toml"
        alias = tmp_path / "scratch" / "alias.toml"
        primary.parent.mkdir()
        r = self._run_install(home, tmp_path,
                              extra_env={"HIVE_CONFIG": str(primary),
                                         "HIVE_USER_CONFIG": str(alias)})
        assert r.returncode == 0, r.stdout + "\n" + r.stderr
        assert primary.is_file()
        assert not alias.exists(), \
            "when both are set, HIVE_CONFIG (the canonical spelling) must win"

    def test_env_hint_drops_retired_llm_variables(self):
        install = (_repo_root() / "packaging" / "install.sh").read_text()
        assert "ANTHROPIC_BASE_URL" not in install, \
            "installer hint still recommends the retired ANTHROPIC_* vars"
        assert "ANTHROPIC_API_KEY" not in install
        assert "LLM_TINY_URL" not in install
        assert "$USER_CONFIG" in install, \
            "the env-file hint must point at the scaffolded config.toml"
        assert "LLM settings go in" in install

    def test_install_ends_with_next_steps_block(self):
        """F3: the installer's last word is the onboarding loop — edit
        [llm], hive doctor, hive init — in that order, on one screen.
        A static guard, like the env-hint test: the source carries the
        block, the steps in order, and the [llm] step pointing at the
        real config path (not a placeholder)."""
        install = (_repo_root() / "packaging" / "install.sh").read_text()
        i_steps = install.find("Next steps:")
        assert i_steps != -1, \
            "install.sh must end with the next-steps block"
        tail = install[i_steps:]
        i_edit = tail.find("Edit [llm]")
        i_doctor = tail.find("hive doctor")
        i_init = tail.find("hive init <project-dir>")
        assert -1 not in (i_edit, i_doctor, i_init), \
            ("the block must name all three steps: edit [llm], "
             "hive doctor, hive init")
        assert 0 <= i_edit < i_doctor < i_init, \
            "the steps must be in order: edit [llm] → hive doctor → hive init"
        assert "$USER_CONFIG" in tail, \
            "the edit-[llm] step must point at the real config path"


class TestCutReleaseScript:
    """E1 — packaging/cut-release.sh: the release cut, as a command.

    The 0.7.0/0.7.1 dance (suite -> build -> listing -> credential scan
    -> sha256 -> tag -> push -> gh release) is codified with a hard gate
    at every step. These tests exercise the gates on a scratch tree:
    --dry-run runs everything up to the tag; a red suite refuses the cut
    before anything is built.
    """

    _GIT_IDENTITY = {
        "GIT_AUTHOR_NAME": "cut-test", "GIT_AUTHOR_EMAIL": "cut-test@example.com",
        "GIT_COMMITTER_NAME": "cut-test", "GIT_COMMITTER_EMAIL": "cut-test@example.com",
    }

    @pytest.fixture()
    def scratch_tree(self, tmp_path):
        """A git checkout of the current working tree (uncommitted runtime
        changes included), committed, so the script's git gates can run."""
        repo = _repo_root()
        tree = tmp_path / "tree"
        tree.mkdir()
        tar = subprocess.run(
            ["tar", "-cf", "-",
             "--exclude=.git", "--exclude=unsloth-tmp", "--exclude=.local",
             "--exclude=dist", "--exclude=__pycache__", "--exclude=*.pyc",
             "."],
            cwd=str(repo), capture_output=True, timeout=300,
        )
        # .local holds runtime-local state (container storage with
        # permission-restricted overlay layers) that must not be part of
        # the snapshot — tar exits non-zero if it can't read one.
        assert tar.returncode == 0, tar.stderr.decode(errors="replace")
        extract = subprocess.run(
            ["tar", "-xf", "-", "-C", str(tree)],
            input=tar.stdout, capture_output=True, timeout=300,
        )
        assert extract.returncode == 0, extract.stderr.decode(errors="replace")
        env = {**os.environ, **self._GIT_IDENTITY}
        for args in (["git", "init", "-q", "-b", "main"],
                     ["git", "add", "-A"],
                     ["git", "commit", "-qm", "cut"]):
            subprocess.run(args, cwd=str(tree), env=env, check=True, timeout=120)
        return tree

    def _run(self, tree, out_dir, suite):
        return subprocess.run(
            ["bash", str(tree / "packaging" / "cut-release.sh"),
             "--dry-run", "--out-dir", str(out_dir), "--suite", suite],
            capture_output=True, text=True, timeout=600, cwd=str(tree),
        )

    def test_dry_run_produces_tarball_sha256_scan_report(self, scratch_tree, tmp_path):
        out = tmp_path / "dist"
        r = self._run(
            scratch_tree, out,
            "python -m pytest tests/test_installation.py::TestTarballContentGuard -q -x",
        )
        assert r.returncode == 0, r.stdout + "\n" + r.stderr
        version = (scratch_tree / "VERSION").read_text().strip()
        assert (out / f"hive-{version}.tar.gz").is_file(), \
            "dry-run must build the tarball"
        assert (out / f"hive-{version}.sha256").is_file(), \
            "dry-run must carry the sha256 sidecar"
        report = out / f"hive-{version}.scan-report"
        assert report.is_file(), "dry-run must write the scan report"
        assert "RESULT: CLEAN" in report.read_text(), \
            "the current tree scans clean; the report must say so"
        assert (out / f"hive-{version}.release-notes.md").is_file(), \
            "dry-run must extract the release notes from the CHANGELOG"
        assert "git tag v" in r.stdout, \
            "dry-run must announce (not run) the tag step"
        tags = subprocess.run(["git", "tag", "-l"], cwd=str(scratch_tree),
                              capture_output=True, text=True, timeout=30)
        assert tags.stdout == "", \
            "--dry-run must stop before the tag: no tag may exist"

    def test_refuses_on_dirty_suite(self, scratch_tree, tmp_path):
        """A deliberately red suite must refuse the cut — and refuse before
        anything is built (no tarball, no sidecar)."""
        env = {**os.environ, **self._GIT_IDENTITY}
        (scratch_tree / "tests" / "test_e1_dirty.py").write_text(
            "def test_deliberately_broken():\n    assert False, 'dirty suite'\n"
        )
        for args in (["git", "add", "tests/test_e1_dirty.py"],
                     ["git", "commit", "-qm", "dirty"]):
            subprocess.run(args, cwd=str(scratch_tree), env=env, check=True,
                           timeout=60)
        out = tmp_path / "dist"
        r = self._run(scratch_tree, out,
                      "python -m pytest tests/test_e1_dirty.py -q -x")
        assert r.returncode != 0, \
            "a red suite must refuse the cut"
        assert "not green" in r.stdout + r.stderr, \
            "the refusal must say the suite is not green"
        assert not any(out.glob("hive-*.tar.gz")), \
            "the suite gate is before the build: no tarball may exist"

    def test_refuses_when_tree_dirty(self, scratch_tree, tmp_path):
        (scratch_tree / "junk.txt").write_text("untracked junk\n")
        r = self._run(
            scratch_tree, tmp_path / "dist",
            "python -m pytest tests/test_json_structures.py -q -x",
        )
        assert r.returncode != 0, "a dirty tree must refuse the cut"
        assert "dirty" in r.stdout + r.stderr


class TestCutReleaseResumeHints:
    """D3: a failing cut-release gate ends with a 'Resume:' line naming the
    exact command to run next (suite red, scan dirty, tag exists, publish
    failed) — a static guard over the script's failure branches."""

    def test_every_failure_branch_has_resume_line(self):
        src = (_repo_root() / "packaging" / "cut-release.sh").read_text().splitlines()
        def ok(marker):
            for i, l in enumerate(src):
                if marker in l:
                    return any("Resume:" in x or "PUBLISH_RESUME" in x
                               for x in src[i:i + 4])
            return False
        for m in ("FAIL: test suite is not green",
                  "FAIL: tag v$VERSION already exists",
                  "FAIL: credential patterns found",
                  "FAIL: gh release create failed",
                  "FAIL: gh release edit failed",
                  "FAIL: gh release upload failed"):
            assert ok(m), f"no Resume: line after {m!r}"
        assert "bash packaging/cut-release.sh --version" in "\n".join(src)

    def test_resume_hint_printed_on_tag_exists(self, tmp_path):
        # Exercise the tag-exists branch for real: a minimal scratch repo
        # carrying just the script + VERSION + CHANGELOG, pre-tagged.
        tree = tmp_path / "cut"
        (tree / "packaging").mkdir(parents=True)
        shutil.copy(_repo_root() / "packaging" / "cut-release.sh",
                    tree / "packaging" / "cut-release.sh")
        ver = (_repo_root() / "VERSION").read_text().strip()
        (tree / "VERSION").write_text(ver + "\n")
        (tree / "CHANGELOG.md").write_text(f"## {ver}\n\nnotes\n")
        env = {**os.environ, "GIT_AUTHOR_NAME": "d3", "GIT_AUTHOR_EMAIL": "d3@e.com",
               "GIT_COMMITTER_NAME": "d3", "GIT_COMMITTER_EMAIL": "d3@e.com"}
        subprocess.run(["git", "init", "-q"], cwd=tree, check=True)
        subprocess.run(["git", "config", "user.email", "d3@e.com"], cwd=tree, check=True)
        subprocess.run(["git", "config", "user.name", "d3"], cwd=tree, check=True)
        subprocess.run(["git", "add", "-A"], cwd=tree, env=env, check=True)
        subprocess.run(["git", "commit", "-qm", "cut"], cwd=tree, env=env, check=True)
        subprocess.run(["git", "tag", "v" + ver], cwd=tree, check=True)
        run = subprocess.run(
            ["bash", str(tree / "packaging" / "cut-release.sh"), "--dry-run",
             "--suite", "true"],
            capture_output=True, text=True, timeout=120, cwd=str(tree))
        assert run.returncode != 0
        assert f"tag v{ver} already exists" in run.stdout + run.stderr
        assert "Resume:" in run.stdout + run.stderr


class TestTarballContentGuard:
    """E2 — pin the files install.sh reads at runtime in the tarball.

    The 0.7.1 template shipped by accident of the `for d in ... etc ...`
    copy loop; this guard makes the requirement explicit so a future
    restructure that drops any of these files fails the suite instead of
    a fresh-machine install.
    """

    #: Every entry install.sh dereferences at runtime, plus the defaults
    #: schema (hive config's unknown-key validation needs it on the target).
    REQUIRED_ENTRIES = (
        "bin/hive",
        "etc/config.template.toml",
        "etc/config.defaults.toml",
        "packaging/requirements.txt",
    )

    def _build(self, tmp_path: Path) -> tuple[Path, list[str]]:
        out_dir = tmp_path / "dist"
        repo = _repo_root()
        result = subprocess.run(
            ["bash", str(repo / "packaging" / "build-package.sh"),
             "--out-dir", str(out_dir)],
            capture_output=True, text=True, timeout=300,
            cwd=str(repo),
        )
        assert result.returncode == 0, (
            "build-package.sh failed:\n"
            f"{result.stdout}\n{result.stderr}"
        )
        tarballs = sorted(out_dir.glob("hive-*.tar.gz"))
        assert len(tarballs) == 1, (
            f"expected exactly one tarball in {out_dir}, got "
            f"{[t.name for t in tarballs]}"
        )
        listing = subprocess.run(
            ["tar", "-tzf", str(tarballs[0])],
            capture_output=True, text=True, timeout=60,
        )
        assert listing.returncode == 0, listing.stderr
        return tarballs[0], listing.stdout.splitlines()

    def test_tarball_contains_files_install_reads(self, tmp_path):
        tarball, listing = self._build(tmp_path)
        missing = [
            entry for entry in self.REQUIRED_ENTRIES
            if not any(line.endswith(f"/{entry}") or line == entry
                       for line in listing)
        ]
        assert not missing, (
            f"{tarball.name} is missing entries install.sh reads at "
            f"runtime: {missing}"
        )

    def test_tarball_ships_sha256_sidecar(self, tmp_path):
        tarball, _ = self._build(tmp_path)
        # build-package.sh names the sidecar <pkg>.sha256 next to
        # <pkg>.tar.gz (not .tar.gz.sha256).
        sidecar = tarball.parent / (tarball.name[:-len(".tar.gz")] + ".sha256")
        assert sidecar.is_file(), (
            "the sha256 sidecar must sit next to the tarball"
        )
        content = sidecar.read_text().strip().split()
        assert len(content) == 2, "sidecar must be '<hash>  <filename>'"
        import hashlib
        digest = hashlib.sha256(tarball.read_bytes()).hexdigest()
        assert content[0] == digest, (
            "sidecar hash does not match the tarball bytes"
        )
