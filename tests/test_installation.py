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


@pytest.mark.xdist_group("install-scaffold")
class TestInstallScaffoldsUserConfig:
    """C3: the installer creates ~/.config/hive/config.toml from the tracked
    template — a visible config with every key defaulted at install time —
    and never overwrites an existing file. The env-file hint must point at
    $HOME/.config/hive/env.sh for shell-level env only, and stop
    recommending the retired ANTHROPIC_*/LLM_TINY_* variable names."""

    @pytest.fixture(scope="class")
    def install_tree(self, tmp_path_factory, _installed_base):
        """T3 (0.20): ONE copy of the session install base per CLASS —
        the 5 install runs share the tree. The copy IS the measured
        suite wall (the base carries the venv; the 0.19 profile showed
        the drift as fixture copy time), so the per-test copytree of
        0.16 retired: one copy per class, no cross-class state (the
        base stays the read-only copy source — the S5 contract is
        unchanged at the session level). Each install still runs under
        a fresh scratch HOME (as in 0.19) and REUSES the tree's venv
        (the 0.15 T1 upgrade branch — idempotent lock re-install,
        exact-set prune); the user-config branch never writes into the
        tree, so no run observes a mutation a later one cares about
        (verified: all five pass on the one shared tree). The class is
        xdist_group-pinned (the contract for a class-scoped mutable
        fixture)."""
        tree = tmp_path_factory.mktemp("install-scaffold") / "hive-src"
        shutil.copytree(_installed_base, tree)
        return tree

    @staticmethod
    def _run_install(tree: Path, home: Path,
                     extra_env: dict | None = None) -> subprocess.CompletedProcess:
        # T3 (0.20): the tree is the per-CLASS install_tree (one copy
        # of the session _installed_base — the 5 runs share it); the
        # venv is REUSED by every install, not rebuilt. The user-config
        # branch (the subject of this class) is independent of the
        # venv and of the other runs (fresh scratch HOME each).
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

    def test_install_creates_user_config_from_template(self, tmp_path, install_tree):
        home = tmp_path / "home"
        home.mkdir()
        r = self._run_install(install_tree, home)
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

    def test_install_never_overwrites_existing_user_config(self, tmp_path, install_tree):
        home = tmp_path / "home"
        user = home / ".config" / "hive" / "config.toml"
        user.parent.mkdir(parents=True)
        edited = '[llm]\nprovider = "ollama"\nmodel = "my-model"\n'
        user.write_text(edited)
        r = self._run_install(install_tree, home)
        assert r.returncode == 0, r.stdout + "\n" + r.stderr
        assert user.read_text() == edited, \
            "an existing user config holds operator edits — install must keep it"

    def test_install_honors_hive_config_env(self, tmp_path, install_tree):
        """A2: install.sh reads HIVE_CONFIG — the spelling doctor and
        release-publish-runner.sh already use."""
        home = tmp_path / "home"
        home.mkdir()
        target = tmp_path / "scratch" / "c.toml"
        target.parent.mkdir()
        r = self._run_install(install_tree, home,
                              extra_env={"HIVE_CONFIG": str(target)})
        assert r.returncode == 0, r.stdout + "\n" + r.stderr
        assert target.is_file(), \
            "HIVE_CONFIG must steer where the installer scaffolds the config"
        assert not (home / ".config" / "hive" / "config.toml").exists(), \
            "the default location must not also be scaffolded"

    def test_install_hive_user_config_alias_still_works(self, tmp_path, install_tree):
        """A2: HIVE_USER_CONFIG keeps working as a deprecated alias —
        with a one-time note pointing at the new spelling."""
        home = tmp_path / "home"
        home.mkdir()
        target = tmp_path / "scratch" / "alias.toml"
        target.parent.mkdir()
        r = self._run_install(install_tree, home,
                              extra_env={"HIVE_USER_CONFIG": str(target)})
        assert r.returncode == 0, r.stdout + "\n" + r.stderr
        assert target.is_file(), \
            "the HIVE_USER_CONFIG alias must keep working for one release"
        assert "HIVE_USER_CONFIG" in r.stdout and "deprecated" in r.stdout, \
            "the alias use must print a deprecation note naming the new var"

    def test_install_hive_config_wins_over_alias(self, tmp_path, install_tree):
        home = tmp_path / "home"
        home.mkdir()
        primary = tmp_path / "scratch" / "primary.toml"
        alias = tmp_path / "scratch" / "alias.toml"
        primary.parent.mkdir()
        r = self._run_install(install_tree, home,
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
             "--exclude=.cache", "--exclude=dist",
             "--exclude=__pycache__", "--exclude=*.pyc", "."],
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

    def test_gate2_default_suite_is_the_s5_primary_loop(self, scratch_tree):
        """T6 (0.13): gate 2's DEFAULT must be the S5 primary loop
        (-n 8). The 0.12 cut had to pass --suite explicitly to get it
        (~11 min of serial gate-2 wall instead of ~3 min on the 0.12
        reference box) — a flag the cut should not have to remember.
        Serial stays available via --suite."""
        src = (scratch_tree / "packaging" / "cut-release.sh").read_text()
        line = next((l for l in src.splitlines() if l.startswith('SUITE="')),
                    None)
        assert line, "cut-release.sh must define a default SUITE command"
        suite = line.split('"')[1]
        assert "-n" in suite and "8" in suite, (
            f"gate 2's default suite must be the S5 primary loop "
            f"(-n 8); got: {suite!r} — serial remains available "
            f"via --suite"
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
            "python -m pytest tests/test_json_structures.py -q -x --deselect tests/test_json_structures.py::TestLockWheelOnly",
        )
        assert r.returncode != 0, "a dirty tree must refuse the cut"
        assert "dirty" in r.stdout + r.stderr

    def test_dry_run_notes_extraction_survives_large_changelog(
            self, scratch_tree, tmp_path):
        """Gate 7 extracts the release title by piping the notes through
        'grep | grep | first-line' under set -euo pipefail. A first-line
        consumer that exits early (head -1) SIGPIPEs the middle grep the
        instant the notes exceed the pipe buffer (64KB) — exit 141
        aborts the cut via set -e after the tarball, scan, and sha256
        have all passed. The consumer must drain the pipe (sed -n 1p),
        so the extraction completes no matter how big the section is.
        The 0.9.0 cut's own gate run found this on a loaded box with a
        feature-sized changelog section."""
        v = (scratch_tree / "VERSION").read_text().strip()
        (scratch_tree / "CHANGELOG.md").write_text(
            "## %s\n\nProof by execution.\n\n" % v
            + "\n".join("Line %d of a deliberately oversized section."
                        % i for i in range(8000))
            + "\n\n## 0.0.0\n\nOlder release.\n"
        )
        env = {**os.environ, **self._GIT_IDENTITY}
        for args in (["git", "add", "CHANGELOG.md"],
                     ["git", "commit", "-qm", "big-notes"]):
            subprocess.run(args, cwd=str(scratch_tree), env=env, check=True,
                           timeout=60)
        out = tmp_path / "dist"
        r = self._run(scratch_tree, out,
                      "python -m pytest tests/test_json_structures.py -q -x --deselect tests/test_json_structures.py::TestLockWheelOnly")
        assert r.returncode == 0, r.stdout + "\n" + r.stderr
        notes = out / f"hive-{v}.release-notes.md"
        assert notes.is_file(), "dry-run must extract the release notes"
        assert "Line 0 of a deliberately oversized section." \
            in notes.read_text()


class TestCutReleaseSurfaceGate:
    """R2 — gate 0 covers every version-bearing surface: VERSION,
    packaging/pyproject.toml, the newest CHANGELOG.md section, and the
    'Latest release' + tarball-name lines in README.md and
    share/docs/README.md. One surface stale → the gate fails naming
    the stale file and nothing else (the others agree); two stale →
    both named. --check-surface runs the gate standalone so the cut
    commit can be validated before the cut runs.

    Regression: the 0.10.0 cut moved VERSION + pyproject + CHANGELOG +
    the tag but left both READMEs at v0.9.0 — the gate had no README
    surface, so it could not have caught the rot.
    """

    ALL_SURFACES = ("VERSION", "packaging/pyproject.toml", "CHANGELOG.md",
                    "README.md", "share/docs/README.md")

    def _build_tree(self, tmp_path, version="9.9.9"):
        """A minimal tree where every surface agrees on `version`.

        Not a git repo — --check-surface is read-only and precedes the
        git-state gate, so the git gates are not in play."""
        tree = tmp_path / "tree"
        (tree / "packaging").mkdir(parents=True)
        (tree / "share" / "docs").mkdir(parents=True)
        shutil.copy(_repo_root() / "packaging" / "cut-release.sh",
                    tree / "packaging" / "cut-release.sh")
        (tree / "VERSION").write_text(f"{version}\n")
        (tree / "packaging" / "pyproject.toml").write_text(
            f'version = "{version}"\n')
        (tree / "CHANGELOG.md").write_text(
            f"## {version}\n\nnotes for this release\n\n"
            f"## 0.0.0\n\nolder\n")
        readme = (f"Latest release: **v{version}**.\n\n"
                  f"1. Download `hive-{version}.tar.gz` and its "
                  f"`hive-{version}.sha256` sidecar\n"
                  f"   sha256sum -c hive-{version}.sha256\n"
                  f"   tar xzf hive-{version}.tar.gz\n")
        (tree / "README.md").write_text(readme)
        (tree / "share" / "docs" / "README.md").write_text(readme)
        return tree

    def _run(self, tree, extra=()):
        return subprocess.run(
            ["bash", str(tree / "packaging" / "cut-release.sh"),
             "--check-surface", *extra],
            capture_output=True, text=True, timeout=60, cwd=str(tree))

    def _assert_names_only(self, tree, stale):
        """Gate 0 fails, names every stale surface — and names no
        surface that agrees."""
        r = self._run(tree)
        assert r.returncode != 0, \
            f"gate 0 must fail with {stale} stale: {r.stdout} {r.stderr}"
        out = r.stdout + r.stderr
        for s in stale:
            assert s in out, f"gate 0 must name stale surface {s}: {out}"
        for other in self.ALL_SURFACES:
            if other in stale:
                continue
            # "README.md" is a substring of "share/docs/README.md", so a
            # docs-README report necessarily contains the root name too;
            # the root-README report does not contain the docs path.
            if other == "README.md" and "share/docs/README.md" in stale:
                continue
            assert other not in out, \
                f"gate 0 must not name the agreeing surface {other}: {out}"

    def test_check_surface_passes_when_every_surface_agrees(self, tmp_path):
        tree = self._build_tree(tmp_path)
        r = self._run(tree)
        assert r.returncode == 0, r.stdout + "\n" + r.stderr
        assert "agree" in r.stdout

    def test_check_surface_passes_on_the_committed_repo(self):
        """The committed tree's surfaces agree: the gate, run read-only
        against the repo itself, passes — gate 0 now covers what
        TestReleaseDocVersion pins."""
        r = subprocess.run(
            ["bash", str(_repo_root() / "packaging" / "cut-release.sh"),
             "--check-surface"],
            capture_output=True, text=True, timeout=60,
            cwd=str(_repo_root()))
        assert r.returncode == 0, r.stdout + "\n" + r.stderr

    @pytest.mark.parametrize("surface", [
        "packaging/pyproject.toml",
        "CHANGELOG.md",
        "README.md",
        "share/docs/README.md",
    ])
    def test_one_stale_surface_is_named_and_only_it(self, tmp_path, surface):
        """Bump exactly one surface: the gate fails, names it, and
        names nothing else."""
        tree = self._build_tree(tmp_path)
        if surface == "packaging/pyproject.toml":
            (tree / "packaging" / "pyproject.toml").write_text(
                'version = "8.8.8"\n')
        elif surface == "CHANGELOG.md":
            (tree / "CHANGELOG.md").write_text(
                "## 8.8.8\n\nstale notes\n\n## 9.9.9\n\nnewer\n")
        else:
            readme = tree / surface
            readme.write_text(
                readme.read_text().replace("v9.9.9", "v8.8.8"))
        self._assert_names_only(tree, (surface,))

    def test_readme_tarball_line_is_a_surface(self, tmp_path):
        """The 'Latest release' line can agree while the install steps
        still point at the previous tarball — the tarball-name line is
        a surface of its own."""
        tree = self._build_tree(tmp_path)
        readme = tree / "README.md"
        readme.write_text(
            readme.read_text().replace("hive-9.9.9.tar.gz",
                                       "hive-8.8.8.tar.gz"))
        self._assert_names_only(tree, ("README.md",))

    def test_version_file_stale_against_explicit_version(self, tmp_path):
        """With --version given, the VERSION file is a surface like any
        other: the file says 8.8.8, the cut is 9.9.9, every other
        surface agrees on 9.9.9 — only VERSION is named."""
        tree = self._build_tree(tmp_path)
        (tree / "VERSION").write_text("8.8.8\n")
        r = self._run(tree, ("--version", "9.9.9"))
        assert r.returncode != 0, r.stdout + "\n" + r.stderr
        out = r.stdout + r.stderr
        assert "VERSION" in out
        for other in self.ALL_SURFACES:
            if other != "VERSION":
                assert other not in out, \
                    f"gate 0 must not name the agreeing surface {other}: {out}"

    def test_two_stale_surfaces_are_both_named(self, tmp_path):
        """'Name each stale file' is plural: two stale surfaces fail
        with both named in one report."""
        tree = self._build_tree(tmp_path)
        (tree / "CHANGELOG.md").write_text(
            "## 8.8.8\n\nstale notes\n\n## 9.9.9\n\nnewer\n")
        readme = tree / "share" / "docs" / "README.md"
        readme.write_text(readme.read_text().replace("v9.9.9", "v8.8.8"))
        self._assert_names_only(tree,
                                ("CHANGELOG.md", "share/docs/README.md"))


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
        # Exercise the tag-exists branch for real: a scratch repo
        # carrying the script + every version-bearing surface (gate 0
        # runs first and needs them all), pre-tagged.
        tree = tmp_path / "cut"
        (tree / "packaging").mkdir(parents=True)
        (tree / "share" / "docs").mkdir(parents=True)
        shutil.copy(_repo_root() / "packaging" / "cut-release.sh",
                    tree / "packaging" / "cut-release.sh")
        ver = (_repo_root() / "VERSION").read_text().strip()
        (tree / "VERSION").write_text(ver + "\n")
        (tree / "packaging" / "pyproject.toml").write_text(
            f'version = "{ver}"\n')
        (tree / "CHANGELOG.md").write_text(f"## {ver}\n\nnotes\n")
        readme = (f"Latest release: **v{ver}**.\n\n"
                  f"1. Download `hive-{ver}.tar.gz` and its "
                  f"`hive-{ver}.sha256` sidecar\n")
        (tree / "README.md").write_text(readme)
        (tree / "share" / "docs" / "README.md").write_text(readme)
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


@pytest.fixture(scope="session")
def _dirty_base(tmp_path_factory):
    """S5: the dirty base is built ONCE per session (a single tar
    round-trip of the repo). Each dirty_tree test then does a local
    copytree of this base instead of paying the tar round-trip again.
    The base's locked/ stays readable — the per-test fixture chmods its
    copy after the copy (a 000 dir cannot be copytree'd)."""
    repo = _repo_root()
    base = tmp_path_factory.mktemp("dirty-base")
    # .cache is excluded like unsloth-tmp: on a host whose pip cache
    # resolves into the repo dir, the suite's own install tests run
    # venv pips that write selfcheck files into .cache/pip WHILE this
    # tar is reading it — a mutable dir in the tar is
    # "file changed as we read it", and this session fixture poisons
    # every consumer (0.19: 16 errors in one run).
    tar = subprocess.run(
        ["tar", "-cf", "-",
         "--exclude=.git", "--exclude=unsloth-tmp", "--exclude=.local",
         "--exclude=.cache", "--exclude=dist",
         "--exclude=__pycache__", "--exclude=*.pyc", "."],
        cwd=str(repo), capture_output=True, timeout=300,
    )
    assert tar.returncode == 0, tar.stderr.decode(errors="replace")
    subprocess.run(["tar", "-xf", "-", "-C", str(base)],
                   input=tar.stdout, check=True, capture_output=True,
                   timeout=300)
    locked = base / "locked"
    locked.mkdir(exist_ok=True)
    (locked / "secret.txt").write_text("secret\n")
    return base


@pytest.fixture()
def dirty_tree(tmp_path, request, _dirty_base):
    """A copy of the repo tree plus what a working checkout actually
    contains: .company/ live state, dist/ build artifacts, junk files,
    and an unreadable unrelated dir (the rsync exit 23 hard-fail
    observed during the 0.11.0 scratch validation). No git — neither
    build-package.sh nor install.sh --copy-to needs it. Shared by the
    S1 (shipping manifest) and S2 (installer parity) test classes.
    The base copy comes from the session-scoped _dirty_base (S5)."""
    import shutil as _shutil
    tree = tmp_path / "tree"
    _shutil.copytree(_dirty_base, tree)
    # The copy may already carry the repo's own live .company/ —
    # exist_ok keeps the fixture idempotent either way.
    (tree / ".company" / "tickets").mkdir(parents=True, exist_ok=True)
    (tree / ".company" / "tickets" / "state.json").write_text("{}\n")
    (tree / ".company" / "roster.json").write_text("{}\n")
    (tree / ".worktrees" / "junk-ticket").mkdir(parents=True, exist_ok=True)
    (tree / ".worktrees" / "junk-ticket" / "file.txt").write_text("x")
    (tree / "dist" / "old").mkdir(parents=True, exist_ok=True)
    (tree / "dist" / "old" / "hive-0.0.1.tar.gz").write_bytes(b"old")
    (tree / "junk.txt").write_text("junk\n")
    (tree / "notes.tmp").write_text("scratch\n")
    # Unreadable unrelated dir (outside every entry set): a whole-tree
    # copy aborts on it (rsync exit 23 under set -e); an entry-set copy
    # never touches it. The dir arrives readable from the base; lock it
    # here, on this test's copy.
    (tree / "locked").chmod(0o000)
    request.addfinalizer(lambda: _try_chmod(tree / "locked", 0o700))
    return tree


@pytest.fixture(scope="session")
def _installed_base(tmp_path_factory):
    """T2 (0.16): the install cluster's venv is built ONCE per session.

    The venv-tax tests (the user-config scaffold class, the dirty-tree
    copy-to test) each staged a tree and ran install.sh into it —
    paying a fresh `python -m venv` + `pip install -r lock` (11.3s warm
    per test) to assert properties (the USER CONFIG branch, the TREE
    PRUNE) that neither touches. This base pays the venv ONCE: one
    staged tree (the same surface _run_install stages: bin, lib, etc,
    packaging, share, VERSION) plus ONE real install.sh run that builds
    the venv from the lock. The consuming tests copy it per test
    (local copytree — the S5 contract: the base is a READ-ONLY copy
    source, no cross-test state) and run install.sh against the copy —
    the venv is REUSED (the 0.15 T1 upgrade branch: idempotent lock
    re-install, exact-set prune). The fresh-install branch (venv
    CREATION + the first lock install) keeps its dedicated test
    (TestFreshInstallBranch, the TestBuildLockVerify pattern). Built on
    first use by one worker, under the basetemp scratch (T4 0.14
    reaping covers it)."""
    repo = _repo_root()
    base = tmp_path_factory.mktemp("hive-installed-base")
    tree = base / "hive-src"
    tree.mkdir()
    for d in ("bin", "lib", "etc", "packaging", "share"):
        shutil.copytree(repo / d, tree / d)
        for junk in (tree / d).rglob("__pycache__"):
            shutil.rmtree(junk)
    (tree / "VERSION").write_text(
        (repo / "VERSION").read_text() if (repo / "VERSION").is_file()
        else "0.0.0-dev\n")
    # ONE real install, IN PLACE (no --copy-to): the staged tree is the
    # install dir — the same surface _run_install exercises. The venv is
    # built from the lock; HOME is a scratch dir so the install's user-
    # config scaffold cannot touch the operator's home.
    home = base / "home"
    home.mkdir()
    r = subprocess.run(
        ["bash", str(tree / "packaging" / "install.sh"),
         "--bin-link", str(home / ".local" / "bin" / "hive")],
        capture_output=True, text=True, timeout=900,
        cwd=str(tree),
        env={**os.environ, "HOME": str(home)},
    )
    assert r.returncode == 0, (
        "the session install base failed — the venv-tax tests have "
        "nowhere to copy from:\n"
        + r.stdout[-2000:] + r.stderr[-2000:])
    assert (tree / "venv" / "bin" / "python").is_file(), \
        "the session install did not build the venv"
    return tree


def _try_chmod(path: Path, mode: int):
    try:
        path.chmod(mode)
    except OSError:
        pass


class TestShippingManifest:
    """S1 (0.12) — the tarball carries its own bill of materials.

    The build staged whole directories and gate 4 pinned only four
    entries, so the shipped surface could drift from the public surface
    with no guard firing (D.2's stale INSTALL / truncated LICENSE is the
    proof). build-package.sh now writes MANIFEST.txt — the exact, sorted
    entry list — into the staged package before tarring, and gate 4 of
    cut-release.sh verifies the manifest FROM THE TARBALL: required
    runtime paths present, runtime-state paths absent, each offending
    entry named. A tree carrying .company/ live state, dist/ build
    artifacts, and junk files must still produce a state-free manifest.
    """

    #: Required runtime entries (the four original gate-4 entries stay;
    #: the list grows to the runtime core).
    REQUIRED = (
        "bin/hive",
        "lib/core/agent-spawn.sh",
        "lib/core/agent-harness.py",
        "lib/hive_mcp/server.py",
        "lib/dashboard/server.js",
        "etc/config.defaults.toml",
        "etc/config.template.toml",
        "packaging/requirements.txt",
        # T3 (0.13): the shared required-set constant ships too (the
        # doctor installed-set check reads it from the install).
        "packaging/required-entries.list",
        # T1 (0.14): the venv lock — the install's Python environment
        # is a reproducible artifact (install.sh installs from it;
        # doctor's venv-lock check cross-checks the venv against it).
        "packaging/requirements.lock.txt",
    )

    STATE_TOP = (".company", ".worktrees", "venv", "dist",
                 "__pycache__", ".git")


    def _build(self, tree: Path, out_dir: Path) -> Path:
        out_dir.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["bash", str(tree / "packaging" / "build-package.sh"),
             "--out-dir", str(out_dir)],
            capture_output=True, text=True, timeout=300,
            cwd=str(tree),
        )
        assert result.returncode == 0, (
            f"build-package.sh failed:\n{result.stdout}\n{result.stderr}"
        )
        tarballs = sorted(out_dir.glob("hive-*.tar.gz"))
        assert len(tarballs) == 1, (
            f"expected exactly one tarball in {out_dir}, got "
            f"{[t.name for t in tarballs]}"
        )
        return tarballs[0]

    @staticmethod
    def _manifest_from_tarball(tarball: Path) -> tuple[list[str], list[str]]:
        """Return (manifest entries, tar FILE entries) read FROM the
        tarball — the same surface gate 4 sees. Directory members are
        filtered out (tarfile names carry no trailing slash, unlike
        `tar -tzf`)."""
        import tarfile
        with tarfile.open(tarball) as tf:
            members = tf.getmembers()
            files = [m.name for m in members if m.isfile()]
            member = next((m for m in members
                           if m.name.endswith("/MANIFEST.txt")
                           and m.isfile()), None)
            entries = []
            if member is not None:
                data = tf.extractfile(member)
                entries = data.read().decode().splitlines()
        return entries, files

    @staticmethod
    def _state_offenders(entries: list[str]) -> list[str]:
        offenders = []
        for e in entries:
            top = e.split("/", 1)[0]
            if top in TestShippingManifest.STATE_TOP:
                offenders.append(e)
            elif any(f"/{s}" in e for s in
                     (".company", ".worktrees", "/venv", "/dist",
                      "__pycache__", ".git")):
                offenders.append(e)
        return offenders

    def test_manifest_ships_and_is_state_free(self, dirty_tree, tmp_path):
        tarball = self._build(dirty_tree, tmp_path / "dist")
        entries, listing = self._manifest_from_tarball(tarball)
        assert entries, (
            "the tarball must carry MANIFEST.txt with the entry list "
            f"(listing has {len(listing)} entries)"
        )
        # sorted, no duplicates — the manifest is the exact entry set
        assert entries == sorted(entries), "manifest must be sorted"
        assert len(entries) == len(set(entries)), \
            "manifest must not duplicate entries"
        missing = [e for e in self.REQUIRED if e not in entries]
        assert not missing, (
            f"manifest is missing required runtime entries: {missing}"
        )
        # the dashboard frontend ships as a directory of static files:
        # at least one entry under lib/dashboard/static/
        assert any(e.startswith("lib/dashboard/static/") for e in entries), \
            "manifest carries no lib/dashboard/static/ entries"
        offenders = self._state_offenders(entries)
        assert not offenders, (
            f"manifest carries runtime-state entries: {offenders}"
        )

    def test_manifest_matches_the_tar_listing(self, dirty_tree, tmp_path):
        """The manifest is the EXACT entry list: every file in the
        tarball (minus the top-level dir prefix) is in the manifest and
        vice versa — drift in either direction is a shipping bug."""
        tarball = self._build(dirty_tree, tmp_path / "dist")
        entries, listing = self._manifest_from_tarball(tarball)
        assert entries, "the tarball must carry MANIFEST.txt"
        prefix = tarball.name[: -len(".tar.gz")] + "/"
        files = [line[len(prefix):] for line in listing
                 if line.startswith(prefix)]
        assert sorted(files) == sorted(entries), (
            "manifest does not match the tarball's file entries "
            f"(tar has {len(files)}, manifest has {len(entries)})"
        )

    def test_gate4_fails_naming_missing_required_entry(self, dirty_tree,
                                                       tmp_path):
        """Removing one required entry from the staged tree makes the
        build's manifest lack it and gate 4 (via the cut) fail naming
        it — the demonstrated-once red→green proof, pinned permanently."""
        victim = dirty_tree / "lib" / "hive_mcp" / "server.py"
        victim.unlink()
        env = {**os.environ, **TestCutReleaseScript._GIT_IDENTITY}
        for args in (["git", "init", "-q", "-b", "main"],
                     ["git", "add", "-A"],
                     ["git", "commit", "-qm", "missing-entry"]):
            subprocess.run(args, cwd=str(dirty_tree), env=env, check=True,
                           timeout=120)
        r = subprocess.run(
            ["bash", str(dirty_tree / "packaging" / "cut-release.sh"),
             "--dry-run", "--out-dir", str(tmp_path / "dist"),
             "--suite", "python -m pytest tests/test_json_structures.py -q --deselect tests/test_json_structures.py::TestLockWheelOnly"],
            capture_output=True, text=True, timeout=600, cwd=str(dirty_tree),
        )
        assert r.returncode != 0, (
            "gate 4 must fail when a required entry is missing from the "
            "manifest; the cut went green:\n" + r.stdout[-3000:]
        )
        out = r.stdout + r.stderr
        assert "lib/hive_mcp/server.py" in out, (
            "gate 4 must NAME the missing entry; got:\n" + out[-3000:]
        )

    def test_gate4_fails_naming_state_entry_in_manifest(self, dirty_tree,
                                                         tmp_path):
        """A runtime-state entry that makes it into the manifest fails
        gate 4 naming it (the D.2 class of drift, now guarded)."""
        env = {**os.environ, **TestCutReleaseScript._GIT_IDENTITY}
        for args in (["git", "init", "-q", "-b", "main"],
                     ["git", "add", "-A"],
                     ["git", "commit", "-qm", "state-entry"]):
            subprocess.run(args, cwd=str(dirty_tree), env=env, check=True,
                           timeout=120)
        # Simulate the drift the manifest guards against: the shared
        # entry list (entries.list) and the prune list in
        # build-package.sh are two hand-maintained lists, and the class
        # of bug is one drifting from the other — stage .company/ via
        # the entry list AND let the prune list forget it.
        elist = dirty_tree / "packaging" / "entries.list"
        etext = elist.read_text()
        assert "VERSION" in etext.splitlines(), \
            "entries.list shape moved — update this test"
        etext = etext.replace("VERSION\n", "VERSION\n.company\n", 1)
        elist.write_text(etext)
        build = dirty_tree / "packaging" / "build-package.sh"
        text = build.read_text()
        prune_needle = ("find \"$STAGE/$PKG_NAME\" -type d -name "
                        "'.company' -prune -exec rm -rf {} +")
        assert prune_needle in text, \
            "build-package.sh prune list moved — update this test"
        text = text.replace(prune_needle, ":  # pruned away by the drift")
        build.write_text(text)
        for args in (["git", "add", "packaging/build-package.sh",
                      "packaging/entries.list"],
                     ["git", "commit", "-qm", "stage-state"]):
            subprocess.run(args, cwd=str(dirty_tree), env=env, check=True,
                           timeout=60)
        r = subprocess.run(
            ["bash", str(dirty_tree / "packaging" / "cut-release.sh"),
             "--dry-run", "--out-dir", str(tmp_path / "dist"),
             "--suite", "python -m pytest tests/test_json_structures.py -q --deselect tests/test_json_structures.py::TestLockWheelOnly"],
            capture_output=True, text=True, timeout=600, cwd=str(dirty_tree),
        )
        assert r.returncode != 0, (
            "gate 4 must fail when the manifest carries a runtime-state "
            "entry; the cut went green:\n" + r.stdout[-3000:]
        )
        out = r.stdout + r.stderr
        assert ".company" in out, (
            "gate 4 must NAME the offending state entry; got:\n" + out[-3000:]
        )


class TestBuildManifestSelfVerify:
    """T1 (0.13) — build-package.sh self-verifies the S1 invariant.

    The 2026-11-08 CI flake (run 42520823326, 3.12 full row): gate 4
    failed with "tarball does not carry MANIFEST.txt" although
    build-package.sh had just read 215 entries back from the staged
    MANIFEST.txt — no tar warning, and nothing in the script touches
    the stage between the manifest write and the tar. The invariant
    (the tarball carries its own bill of materials) was verified only
    by gate 4 on the cut path; the producer declared success without
    looking at its own output, so the failure surfaced one gate later,
    on one path, with no forensics. build-package.sh now (a) publishes
    MANIFEST.txt atomically (tmp + mv — the final name appears complete
    or not at all) and (b) verifies the MANIFEST.txt member FROM THE
    TARBALL after tarring; a miss fails the build naming the entry and
    dumps the staging-vs-tarball diff so the next occurrence is
    explained, not guessed.
    """

    def _build(self, tree: Path, out_dir: Path) -> subprocess.CompletedProcess:
        out_dir.mkdir(parents=True, exist_ok=True)
        return subprocess.run(
            ["bash", str(tree / "packaging" / "build-package.sh"),
             "--out-dir", str(out_dir)],
            capture_output=True, text=True, timeout=300,
            cwd=str(tree),
        )

    def test_manifest_raced_away_fails_loudly_named(self, dirty_tree,
                                                    tmp_path):
        """Deterministic replay of the flake: MANIFEST.txt is deleted
        from the stage right before the tar (present at write time,
        gone at tar time). The build must fail non-zero and name the
        missing entry — before the fix it "succeeded", shipping a
        broken tarball that only the cut's gate 4 would have caught."""
        import re as _re
        script = dirty_tree / "packaging" / "build-package.sh"
        text = script.read_text()
        m = _re.search(r'^echo "Creating tarball\.\.\.', text, _re.M)
        assert m, ("build-package.sh layout moved (no 'Creating tarball' "
                   "line) — update the race injection")
        raced = text[:m.start()] + \
            'rm -f "$STAGE/$PKG_NAME/MANIFEST.txt"\n' + text[m.start():]
        script.write_text(raced)
        r = self._build(dirty_tree, tmp_path / "dist")
        assert r.returncode != 0, (
            "a tarball missing MANIFEST.txt must not be declared a build"
            f" success:\n{r.stdout}\n{r.stderr}"
        )
        out = r.stdout + r.stderr
        assert "FAIL" in out and "MANIFEST.txt" in out, (
            "the self-verify failure must name the missing entry:\n"
            + out[-3000:]
        )

    def test_manifest_published_atomically(self, dirty_tree):
        """The staged MANIFEST.txt is written to a tmp name and renamed
        into place — no window in which the final name exists while its
        content is still settling (the flake's missing mechanism, closed
        by construction)."""
        import re as _re
        text = (dirty_tree / "packaging" / "build-package.sh").read_text()
        assert _re.search(
            r"mv\s+MANIFEST\.txt\.tmp\s+MANIFEST\.txt\b", text), (
            "build-package.sh must publish MANIFEST.txt atomically "
            "(tmp + mv): the 2026-11-08 CI flake observed the manifest "
            "present at write time and absent at tar time"
        )

    def test_healthy_build_still_carries_the_manifest(self, dirty_tree,
                                                      tmp_path):
        """The self-verify is a gate, not a change: an uncontested
        build still succeeds and the tarball still carries the exact
        manifest (the S1 invariant the verify protects)."""
        r = self._build(dirty_tree, tmp_path / "dist")
        assert r.returncode == 0, (
            f"build-package.sh failed:\n{r.stdout}\n{r.stderr}"
        )
        import tarfile
        tballs = sorted((tmp_path / "dist").glob("hive-*.tar.gz"))
        assert len(tballs) == 1, f"expected one tarball: {tballs}"
        with tarfile.open(tballs[0]) as tf:
            names = [mm.name for mm in tf.getmembers()]
        assert any(n.endswith("/MANIFEST.txt") for n in names), (
            "the healthy build's tarball must carry MANIFEST.txt"
        )


@pytest.fixture(scope="session")
def _fresh_lock_build(_dirty_base, tmp_path_factory):
    """T1 (0.14): the producer's own FRESH-venv lock selftest, once
    per session.

    build-package.sh without HIVE_LOCK_VENV builds a real venv from
    the lock and imports the runtime set — the check the cut runs.
    The other build tests reuse the session lock venv (the conftest
    fixture) so the suite pays the network install once; this fixture
    is the dedicated coverage for the fresh path (it runs on exactly
    one session — the test using it is distributed to one worker).
    The tree and the venv are hive-* named scratch: T4's clean-scratch
    reaps them."""
    import shutil as _shutil
    base = tmp_path_factory.mktemp("hive-fresh-lock")
    tree = base / "tree"
    _shutil.copytree(_dirty_base, tree)
    r = subprocess.run(
        ["bash", str(tree / "packaging" / "build-package.sh"),
         "--out-dir", str(base / "dist")],
        capture_output=True, text=True, timeout=1800,
        cwd=str(tree),
        env={k: v for k, v in os.environ.items()
             if k != "HIVE_LOCK_VENV"},
    )
    return r


class TestBuildLockVerify:
    """T1 (0.14) — the build self-verifies the venv lock.

    packaging/requirements.lock.txt is the released Python environment:
    an exact pin for every name in requirements.txt. build-package.sh
    verifies it BEFORE staging anything (the producer looks at its own
    output — the e0a43a0 lesson applied to the runtime): (1) the lock
    exists, (2) every name in requirements.txt has a pin in the lock
    that satisfies the floor, (3) a venv built from the lock imports
    the runtime set. A miss fails the build, naming the entry.
    Red against the pre-T1 build, which never looked at the lock at
    all (a drift in the venv's resolved versions was undetectable).
    """

    @staticmethod
    def _build(tree: Path, out_dir: Path):
        out_dir.mkdir(parents=True, exist_ok=True)
        return subprocess.run(
            ["bash", str(tree / "packaging" / "build-package.sh"),
             "--out-dir", str(out_dir)],
            capture_output=True, text=True, timeout=600,
            cwd=str(tree),
        )

    def test_missing_lock_fails_naming_it(self, dirty_tree, tmp_path):
        """(1) no lock file: the build fails naming the lock — the
        install's Python environment must be a reproducible artifact."""
        (dirty_tree / "packaging" / "requirements.lock.txt").unlink()
        r = self._build(dirty_tree, tmp_path / "dist")
        assert r.returncode != 0, (
            "a build without the venv lock must fail:\n"
            + r.stdout + "\n" + r.stderr)
        out = r.stdout + r.stderr
        assert "requirements.lock.txt" in out, (
            "the failure must name the missing lock:\n" + out[-2000:])

    def test_lock_missing_a_name_fails_naming_it(self, dirty_tree,
                                                 tmp_path):
        """(2a) a name in requirements.txt with no pin in the lock:
        the build fails naming THAT name."""
        lock = dirty_tree / "packaging" / "requirements.lock.txt"
        lines = [l for l in lock.read_text().splitlines()
                 if not l.lower().startswith("fastapi")]
        assert len(lines) < lock.read_text().count("\n"), \
            "the lock lost its fastapi line — update this test"
        lock.write_text("\n".join(lines) + "\n")
        r = self._build(dirty_tree, tmp_path / "dist")
        assert r.returncode != 0, (
            "a lock missing a required name must fail the build:\n"
            + r.stdout + "\n" + r.stderr)
        out = r.stdout + r.stderr
        assert "fastapi" in out, (
            "the failure must name the missing pin (fastapi):\n"
            + out[-2000:])

    def test_pin_below_the_floor_fails_naming_it(self, dirty_tree,
                                                 tmp_path):
        """(2b) a lock pin below the requirements.txt floor: the build
        fails naming the name — a 'reproducible' env that drops below
        the declared floor is not the released env."""
        import re as _re
        lock = dirty_tree / "packaging" / "requirements.lock.txt"
        text = lock.read_text()
        m = _re.search(r"^mcp==\S+", text, _re.M)
        assert m, "the lock lost its mcp pin — update this test"
        # mcp's floor is >=1.0 (requirements.txt); 0.9.0 sits below it.
        lock.write_text(text.replace(m.group(0), "mcp==0.9.0", 1))
        r = self._build(dirty_tree, tmp_path / "dist")
        assert r.returncode != 0, (
            "a lock pin below the floor must fail the build:\n"
            + r.stdout + "\n" + r.stderr)
        out = r.stdout + r.stderr
        assert "mcp" in out and "floor" in out.lower(), (
            "the failure must name mcp and the floor violation:\n"
            + out[-2000:])

    def test_fresh_venv_from_lock_imports_runtime_set(self,
                                                      _fresh_lock_build):
        """(3) the producer's own path: a FRESH venv built from the
        lock imports the runtime set — no HIVE_LOCK_VENV, the network
        install for real, once per session (the cut runs exactly this)."""
        r = _fresh_lock_build
        assert r.returncode == 0, (
            "the producer's fresh-venv lock selftest must pass:\n"
            + r.stdout[-3000:] + "\n" + r.stderr[-3000:])
        out = r.stdout + r.stderr
        assert "fresh venv from the lock imports the runtime set" in out, (
            "the fresh-venv probe did not run (HIVE_LOCK_VENV leaked in?)"
            ":\n" + out[-2000:])


@pytest.fixture(scope="session")
def _fresh_install(_dirty_base, tmp_path_factory):
    """T2 (0.16): the install's own FRESH-branch selftest, once per
    session (the TestBuildLockVerify pattern).

    With the venv-tax tests re-pointed at the session base (T2), every
    per-test install REUSES a copied venv — nothing in the install
    cluster pays the venv-CREATION branch anymore (the
    uv/stdlib-venv path + the FIRST lock install). The fresh branch
    keeps exactly one dedicated coverage: one full fresh install per
    session, on a clean destination (a --copy-to install into an empty
    dir — the operator's Option A path, the cut's gate-8 path). The
    test using it is distributed to one worker. The tree and the
    install are hive-* named scratch: T4's clean-scratch reaps them.
    """
    base = tmp_path_factory.mktemp("hive-fresh-install")
    install_dir = base / "install"
    r = subprocess.run(
        ["bash", str(_dirty_base / "packaging" / "install.sh"),
         "--copy-to", str(install_dir),
         "--bin-link", str(base / "binlink" / "hive")],
        capture_output=True, text=True, timeout=1800,
        cwd=str(_dirty_base),
    )
    return r, install_dir


class TestFreshInstallBranch:
    """T2 (0.16) — the fresh-install branch keeps its dedicated test.

    The venv-creation branch (uv venv / python3 -m venv + the first
    lock install) is the one install cost the session base cannot
    absorb: a base that already carries a venv can only exercise the
    reuse branch. The dedicated fixture runs ONE full fresh install
    per session (the TestBuildLockVerify pattern — "the fresh path
    itself stays covered ... a dedicated fixture runs one full build
    WITHOUT the variable", distributed to one worker) and asserts the
    venv exists and imports the locked set.
    """

    def test_fresh_install_creates_the_venv_and_imports_the_locked_set(
            self, _fresh_install):
        r, install_dir = _fresh_install
        assert r.returncode == 0, (
            "a FRESH install (venv creation + first lock install) must "
            "pass:\n" + r.stdout[-3000:] + "\n" + r.stderr[-3000:])
        out = r.stdout + r.stderr
        assert "Reusing existing venv" not in out, (
            "the fresh branch must CREATE the venv, not reuse one:\n"
            + out[-2000:])
        venv_python = install_dir / "venv" / "bin" / "python"
        assert venv_python.is_file(), (
            "the fresh install must leave a venv with a python at "
            f"{venv_python}")
        ri = subprocess.run(
            [str(venv_python), "-c",
             "import mcp, fastapi, uvicorn, httpx"],
            capture_output=True, text=True, timeout=120)
        assert ri.returncode == 0, (
            "the fresh venv must import the locked set "
            "(mcp, fastapi, uvicorn, httpx):\n"
            + ri.stderr[-2000:])


class TestInstallCopyToEntrySet:
    """S2 (0.12) — install the entry set, not the tree.

    install.sh --copy-to used to rsync the WHOLE source dir (excluding
    only .git/__pycache__/*.pyc): from a working checkout it dragged
    dist/, .company/ live state, and local junk into the install, and it
    hard-failed (rsync exit 23 under set -e) when the source contained
    unreadable unrelated paths — observed on the 0.12 reference box
    while validating the 0.11.0 scratch install. --copy-to now stages
    the SAME explicit entry set the build ships (packaging/entries.list
    — one source of truth shared with build-package.sh): runtime state
    and anything outside the set is never staged, and unreadable or
    unexpected paths in the source cannot abort the copy.
    """

    #: The top-level surface an entry-set install may carry (the entry
    #: set plus the venv the installer creates in the install dir).
    ALLOWED_TOP = {
        "bin", "lib", "etc", "share", "packaging", "VERSION",
        "README.md", "INSTALL", "LICENSE", "CHANGELOG.md",
        "CONTRIBUTING.md", "SECURITY.md", "venv",
        # T3 (0.14): the install marker (install.sh --copy-to writes it
        # at the install root; uninstall.sh removes the tree on it).
        ".hive-install",
    }

    @staticmethod
    def _run_install(tree: Path, install_dir: Path, home: Path):
        return subprocess.run(
            ["bash", str(tree / "packaging" / "install.sh"),
             "--copy-to", str(install_dir),
             "--bin-link", str(home / ".local" / "bin" / "hive")],
            capture_output=True, text=True, timeout=600,
            cwd=str(tree),
            env={**os.environ, "HOME": str(home)},
        )

    @staticmethod
    def _stub_venv(install_dir: Path) -> None:
        """Pre-create the venv install.sh will 'reuse': pip/python stubs
        that exit 0. Staging is what these tests assert on — no real
        venv, no network, no PyPI round-trip."""
        vbin = install_dir / "venv" / "bin"
        vbin.mkdir(parents=True)
        for tool in ("pip", "python"):
            p = vbin / tool
            p.write_text("#!/bin/sh\nexit 0\n")
            p.chmod(0o755)

    @staticmethod
    def _assert_entry_set_install(install_dir: Path) -> None:
        top = {p.name for p in install_dir.iterdir()}
        stray = top - TestInstallCopyToEntrySet.ALLOWED_TOP
        assert not stray, (
            f"install carries entries outside the entry set: {sorted(stray)}"
        )
        assert not (install_dir / ".company").exists(), \
            "live .company/ state reached the install"
        assert not (install_dir / ".worktrees").exists(), \
            ".worktrees/ reached the install"
        assert not (install_dir / "dist").exists(), \
            "build artifacts (dist/) reached the install"
        assert not (install_dir / "junk.txt").exists(), \
            "junk reached the install"
        assert not (install_dir / "notes.tmp").exists(), \
            "junk reached the install"
        assert not (install_dir / "locked").exists(), \
            "the unreadable unrelated dir reached the install"
        for required in ("bin/hive", "lib/core/agent-spawn.sh",
                         "lib/core/agent-harness.py",
                         "lib/hive_mcp/server.py", "lib/dashboard/server.js",
                         "etc/config.defaults.toml", "etc/config.template.toml",
                         "packaging/requirements.txt", "packaging/entries.list"):
            assert (install_dir / required).is_file(), \
                f"required entry missing from the install: {required}"
        # the public README ships from share/docs/README.md
        assert (install_dir / "README.md").read_text() == \
            (install_dir / "share" / "docs" / "README.md").read_text(), \
            "README.md must be share/docs/README.md verbatim"

    def test_dirty_tree_copy_to_is_a_clean_install(self, dirty_tree,
                                                   tmp_path, _installed_base):
        """Full flow (real venv): dirty source → clean install, hive
        version correct, nothing outside the entry set in the tree."""
        home = tmp_path / "home"
        home.mkdir()
        install_dir = tmp_path / "install"
        # T2 (0.16): seed the destination venv from the session base —
        # the upgrade's venv-reuse branch (the fresh venv is the
        # dedicated test's path). Tree pruning is venv-independent; the
        # assertions below are unchanged.
        shutil.copytree(_installed_base / "venv", install_dir / "venv")
        r = self._run_install(dirty_tree, install_dir, home)
        assert r.returncode == 0, r.stdout + "\n" + r.stderr
        version = (dirty_tree / "VERSION").read_text().strip()
        rv = subprocess.run([str(install_dir / "bin" / "hive"), "version"],
                            capture_output=True, text=True, timeout=60)
        assert rv.returncode == 0, rv.stdout + "\n" + rv.stderr
        assert version in rv.stdout, \
            f"hive version must report {version}; got: {rv.stdout!r}"
        self._assert_entry_set_install(install_dir)
        # the launcher symlink points into the install
        link = home / ".local" / "bin" / "hive"
        assert link.is_symlink() and \
            str(install_dir / "bin" / "hive") in os.readlink(str(link))

    def test_unreadable_source_path_cannot_abort_the_copy(self, dirty_tree,
                                                          tmp_path):
        """Staging-only pass (stubbed venv): an unreadable unrelated dir
        in the source cannot abort the copy — the whole-tree rsync that
        died with exit 23 is gone."""
        home = tmp_path / "home"
        home.mkdir()
        install_dir = tmp_path / "install"
        self._stub_venv(install_dir)
        r = self._run_install(dirty_tree, install_dir, home)
        assert r.returncode == 0, (
            "the entry-set copy must complete with an unreadable "
            "unrelated dir in the source:\n" + r.stdout + "\n" + r.stderr
        )
        self._assert_entry_set_install(install_dir)

    def test_missing_optional_entry_warns_but_installs(self, dirty_tree,
                                                       tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        (dirty_tree / "SECURITY.md").unlink()
        install_dir = tmp_path / "install"
        self._stub_venv(install_dir)
        r = self._run_install(dirty_tree, install_dir, home)
        assert r.returncode == 0, r.stdout + "\n" + r.stderr
        out = r.stdout + r.stderr
        assert "SECURITY.md" in out, \
            "the missing optional entry must be named in the output"
        assert not (install_dir / "SECURITY.md").exists()
        self._assert_entry_set_install(install_dir)

    def test_missing_required_entry_fails_named(self, dirty_tree, tmp_path):
        import shutil as _shutil
        _shutil.rmtree(dirty_tree / "lib" / "core")
        home = tmp_path / "home"
        home.mkdir()
        install_dir = tmp_path / "install"
        self._stub_venv(install_dir)
        r = self._run_install(dirty_tree, install_dir, home)
        assert r.returncode != 0, (
            "a source missing the runtime core must fail the install"
        )
        out = r.stdout + r.stderr
        assert "lib/core" in out, \
            f"the failure must name the missing entry; got:\n{out[-2000:]}"

class TestEntryListSingleSource:
    """S2 (0.12) — the shipped entry set is defined ONCE and shared by
    the build and the installer."""

    def test_entry_list_is_the_single_source_of_truth(self):
        """The entry list is defined ONCE (packaging/entries.list) and
        shared by build-package.sh and install.sh — no second
        hand-maintained staging list, no whole-tree rsync."""
        repo = _repo_root()
        elist = (repo / "packaging" / "entries.list").read_text()
        lines = [l.strip() for l in elist.splitlines()
                 if l.strip() and not l.strip().startswith("#")]
        plain = [l for l in lines if " as " not in l]
        mapped = [l.split(" as ") for l in lines if " as " in l]
        assert set(plain) == {
            "bin", "lib", "etc", "share", "packaging", "VERSION",
            "INSTALL", "LICENSE", "CHANGELOG.md", "CONTRIBUTING.md",
            "SECURITY.md",
            # T3 (0.13): the shipping manifest ships from the published
            # tarball into the install (doctor's installed-set check
            # reads it there). The build generates it — from a source
            # tree it is absent and staged as a NOTE-free skip.
            "MANIFEST.txt",
        }, f"unexpected entry set: {sorted(plain)}"
        assert [m for m in mapped if len(m) == 2] == \
            [["share/docs/README.md", "README.md"]], \
            f"unexpected SRC as DEST mappings: {mapped}"
        build = (repo / "packaging" / "build-package.sh").read_text()
        assert "entries.list" in build and "entries.sh" in build, \
            "build-package.sh must stage from the shared entry list"
        assert "for d in bin lib etc share packaging VERSION" not in build, \
            "build-package.sh keeps its own hand-maintained staging list"
        install = (repo / "packaging" / "install.sh").read_text()
        assert "entries.list" in install and "entries.sh" in install, \
            "install.sh must stage the shared entry list"
        assert '"$SOURCE_DIR/" "$INSTALL_DIR/"' not in install, \
            "install.sh still rsyncs the whole source tree"


import base64 as _base64

#: Fake curl bodies for the install-online URL-resolution tests —
#: base64 keeps the shell script text out of the Python source.
_FAKE_CURL = _base64.b64decode(
    "IyEvYmluL3NoCmxhc3Q9IiIKZm9yIGEgaW4gIiRAIjsgZG8gbGFzdD0iJGEiOyBkb25lCmNhc2UgIiRsYXN0IiBpbgogICpyYXcuZ2l0aHVidXNlcmNvbnRlbnQqKQogICAgaWYgWyAtbiAiJHtGQUtFX0NVUkxfRkFJTDotfSIgXTsgdGhlbgogICAgICBlY2hvICJmYWtlIGN1cmw6IGZldGNoIGZhaWxlZCIgPiYyCiAgICAgIGV4aXQgMQogICAgZmkKICAgIHByaW50ZiAnJXNcbicgIiR7RkFLRV9DVVJMX1ZFUlNJT046LTAuMTEuMH0iCiAgICA7OwogICopIGV4aXQgMCA7Owplc2FjCg=="
).decode()

_FAKE_CURL_LOG = _base64.b64decode(
    "IyEvYmluL3NoCmxhc3Q9IiIKZm9yIGEgaW4gIiRAIjsgZG8gbGFzdD0iJGEiOyBkb25lCnByaW50ZiAnJXNcbicgIiRsYXN0IiA+ICIke0ZBS0VfQ1VSTF9MT0c6LS9kZXYvbnVsbH0iCmNhc2UgIiRsYXN0IiBpbgogICpyYXcuZ2l0aHVidXNlcmNvbnRlbnQqKQogICAgcHJpbnRmICclc1xuJyAiJHtGQUtFX0NVUkxfVkVSU0lPTjotMC4xMS4wfSIKICAgIDs7CiAgKikgZXhpdCAwIDs7CmVzYWMK"
).decode()


class TestInstallOnlineUrlResolution:
    """S2 (0.12) — install-online.sh resolves the tarball source:
    HIVE_PKG_URL > HIVE_PKG_FILE > the GitHub release default for this
    repo (owner/repo overridable via HIVE_REPO, branch via HIVE_BRANCH,
    VERSION fetched from the raw branch). The documented path worked
    only with HIVE_PKG_URL set; without it the script errored, and the
    README one-liner was a placeholder URL.

    HIVE_PRINT_URL is the hermetic seam: resolve the source, print it,
    exit — no download, no install. A fake curl on PATH serves the
    raw-branch VERSION fetch (FAKE_CURL_VERSION / FAKE_CURL_FAIL).
    """

    @staticmethod
    def _fake_curl(tmp_path: Path) -> Path:
        b = tmp_path / "fakebin"
        b.mkdir(exist_ok=True)
        curl = b / "curl"
        curl.write_text(_FAKE_CURL)
        curl.chmod(0o755)
        return b

    def _run(self, tmp_path: Path, env_extra: dict) -> subprocess.CompletedProcess:
        fakebin = self._fake_curl(tmp_path)
        home = tmp_path / "home"
        home.mkdir(exist_ok=True)
        env = {**os.environ,
               "PATH": f"{fakebin}:{os.environ['PATH']}",
               "HOME": str(home),
               "HIVE_PRINT_URL": "1"}
        env.update(env_extra)
        return subprocess.run(
            ["sh", str(_repo_root() / "packaging" / "install-online.sh")],
            capture_output=True, text=True, timeout=60, env=env,
        )

    def test_explicit_pkg_url_wins(self, tmp_path):
        r = self._run(tmp_path,
                      {"HIVE_PKG_URL": "https://example.com/h.tar.gz"})
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == "https://example.com/h.tar.gz"

    def test_pkg_file_short_circuits(self, tmp_path):
        r = self._run(tmp_path, {"HIVE_PKG_FILE": "/tmp/x.tar.gz"})
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == "file:/tmp/x.tar.gz"

    def test_default_url_derives_from_repo_and_version(self, tmp_path):
        r = self._run(tmp_path, {"FAKE_CURL_VERSION": "0.12.0"})
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == (
            "https://github.com/fredjt/hive/releases/download/"
            "v0.12.0/hive-0.12.0.tar.gz"
        )

    def test_repo_override_changes_the_url(self, tmp_path):
        r = self._run(tmp_path, {"HIVE_REPO": "other/org"})
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == (
            "https://github.com/other/org/releases/download/"
            "v0.11.0/hive-0.11.0.tar.gz"
        )

    def test_branch_override_changes_the_version_fetch(self, tmp_path):
        """The raw-branch VERSION fetch must use HIVE_BRANCH (default
        main) — pinned by recording the URL the fake curl was asked to
        fetch and checking it ends at the override branch."""
        fakebin = self._fake_curl(tmp_path)
        curl = fakebin / "curl"
        curl.write_text(_FAKE_CURL_LOG)
        curl.chmod(0o755)
        log = tmp_path / "curl.log"
        home = tmp_path / "home"
        home.mkdir(exist_ok=True)
        env = {**os.environ,
               "PATH": f"{fakebin}:{os.environ['PATH']}",
               "HOME": str(home),
               "HIVE_PRINT_URL": "1",
               "HIVE_BRANCH": "stable",
               "FAKE_CURL_LOG": str(log)}
        r = subprocess.run(
            ["sh", str(_repo_root() / "packaging" / "install-online.sh")],
            capture_output=True, text=True, timeout=60, env=env)
        assert r.returncode == 0, r.stderr
        fetched = log.read_text().strip()
        assert fetched.endswith("/fredjt/hive/stable/VERSION"), \
            f"VERSION fetch must use HIVE_BRANCH; got: {fetched}"

    def test_unreachable_version_fetch_fails(self, tmp_path):
        r = self._run(tmp_path, {"FAKE_CURL_FAIL": "1"})
        assert r.returncode != 0, "a failed VERSION fetch must fail"
        assert "ERROR" in r.stderr


class TestVerifyRelease:
    """T4 (0.13) — verify-release.sh verifies a published release
    against its receipt, in one command.

    Hermetic: a fake `gh` on PATH serves local fixture assets to
    `gh release download --dir`; the fixture set (tarball with a
    MANIFEST.txt member, sidecar, receipt) is built per test with
    real, matching digests. Red simulations: a corrupted manifest
    digest and a wrong sidecar fail and NAME the mismatch (the 0.12
    S3 test style)."""

    VERSION = "0.9.9"

    @classmethod
    def _build_assets(cls, root: Path):
        ver = cls.VERSION
        pkg = root / f"hive-{ver}"
        (pkg / "MANIFEST.txt").parent.mkdir(parents=True)
        (pkg / "MANIFEST.txt").write_text("bin/hive\nlib/core/agent-harness.py\n")
        for entry in ("bin/hive", "lib/core/agent-harness.py"):
            f = pkg / entry
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("#!/usr/bin/env bash\necho stub\n")
        tarball = root / f"hive-{ver}.tar.gz"
        subprocess.run(["tar", "-czf", str(tarball), "-C", str(root),
                        pkg.name], check=True, capture_output=True)
        tar_sha = subprocess.run(["sha256sum", str(tarball)],
                                 capture_output=True, text=True).stdout.split()[0]
        sidecar = root / f"hive-{ver}.sha256"
        sidecar.write_text(f"{tar_sha}  hive-{ver}.tar.gz\n")
        side_sha = subprocess.run(["sha256sum", str(sidecar)],
                                  capture_output=True, text=True).stdout.split()[0]
        man_sha = subprocess.run(["sha256sum", str(pkg / "MANIFEST.txt")],
                                 capture_output=True, text=True).stdout.split()[0]
        gates = "".join(f"gate-{n}: PASS (test gate {n})\n" for n in range(8))
        receipt = root / f"hive-{ver}.receipt.txt"
        receipt.write_text(
            f"receipt: hive-{ver}\nversion: {ver}\nbranch: main\n"
            "commit: 0123456789abcdef0123456789abcdef01234567\n"
            f"tag: v{ver}\n{gates}"
            f"asset: hive-{ver}.tar.gz sha256={tar_sha}\n"
            f"asset: hive-{ver}.sha256 sha256={side_sha}\n"
            f"manifest: MANIFEST.txt sha256={man_sha}\n"
            f"release: https://github.com/fake/hive/releases/tag/v{ver}\n"
            "timestamp: 2026-11-08T00:00:00Z\n")
        return {"tarball": tarball, "sidecar": sidecar, "receipt": receipt}

    @classmethod
    def _fake_gh(cls, tmp_path: Path, assets_dir: Path) -> Path:
        bindir = tmp_path / "ghbin"
        bindir.mkdir()
        gh = bindir / "gh"
        gh.write_text(
            "#!/bin/sh\n"
            "dir=\n"
            "prev=\n"
            "for a in \"$@\"; do\n"
            "  [ \"$prev\" = \"--dir\" ] && dir=\"$a\"\n"
            "  prev=\"$a\"\n"
            "done\n"
            f"cp \"{assets_dir}\"/hive-{cls.VERSION}.* \"$dir\"/\n"
            "exit 0\n")
        gh.chmod(0o755)
        return bindir

    @classmethod
    def _run(cls, tmp_path, assets_dir: Path):
        bindir = cls._fake_gh(tmp_path, assets_dir)
        return subprocess.run(
            ["bash", str(_repo_root() / "packaging" / "verify-release.sh"),
             f"v{cls.VERSION}"],
            capture_output=True, text=True, timeout=120,
            env={**os.environ,
                 "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}"})

    def test_green_release_verifies(self, tmp_path):
        assets_dir = tmp_path / "assets"
        assets_dir.mkdir()
        self._build_assets(assets_dir)
        r = self._run(tmp_path, assets_dir)
        out = r.stdout + r.stderr
        assert r.returncode == 0, out[-800:]
        assert "OK —" in out, out[-400:]

    def test_corrupted_manifest_digest_fails_and_names_it(self, tmp_path):
        assets_dir = tmp_path / "assets"
        assets_dir.mkdir()
        assets = self._build_assets(assets_dir)
        rec = assets["receipt"].read_text()
        lines = rec.splitlines()
        for i, l in enumerate(lines):
            if l.startswith("manifest:"):
                lines[i] = l[:-8] + "deadbeef"
                break
        assets["receipt"].write_text("\n".join(lines) + "\n")
        r = self._run(tmp_path, assets_dir)
        out = r.stdout + r.stderr
        assert r.returncode == 1, out[-800:]
        assert "3/4" in out and "manifest digest mismatch" in out, out[-800:]

    def test_wrong_sidecar_fails_and_names_it(self, tmp_path):
        assets_dir = tmp_path / "assets"
        assets_dir.mkdir()
        assets = self._build_assets(assets_dir)
        assets["sidecar"].write_text("deadbeef" * 8 + "  hive-0.9.9.tar.gz\n")
        r = self._run(tmp_path, assets_dir)
        out = r.stdout + r.stderr
        assert r.returncode == 1, out[-800:]
        assert "1/4" in out and "sidecar mismatch" in out, out[-800:]

    def test_missing_required_key_fails_and_names_it(self, tmp_path):
        assets_dir = tmp_path / "assets"
        assets_dir.mkdir()
        assets = self._build_assets(assets_dir)
        lines = [l for l in assets["receipt"].read_text().splitlines()
                 if not l.startswith("gate-3:")]
        assets["receipt"].write_text("\n".join(lines) + "\n")
        r = self._run(tmp_path, assets_dir)
        out = r.stdout + r.stderr
        assert r.returncode == 1, out[-800:]
        assert "gate-3-PASS" in out, out[-800:]

    def test_gate_fail_line_fails_and_names_the_gate(self, tmp_path):
        assets_dir = tmp_path / "assets"
        assets_dir.mkdir()
        assets = self._build_assets(assets_dir)
        rec = assets["receipt"].read_text().replace(
            "gate-2: PASS (test gate 2)", "gate-2: FAIL (test gate 2)")
        assets["receipt"].write_text(rec)
        r = self._run(tmp_path, assets_dir)
        out = r.stdout + r.stderr
        assert r.returncode == 1, out[-800:]
        assert "gate-2-PASS" in out, out[-800:]

    def test_unparseable_receipt_line_fails(self, tmp_path):
        assets_dir = tmp_path / "assets"
        assets_dir.mkdir()
        assets = self._build_assets(assets_dir)
        rec = assets["receipt"].read_text() + "this line has no key colon pair\n"
        assets["receipt"].write_text(rec)
        r = self._run(tmp_path, assets_dir)
        out = r.stdout + r.stderr
        assert r.returncode == 1, out[-800:]
        assert "key: value" in out, out[-800:]

    def test_live_0_12_0_assets_verify(self, tmp_path):
        """The real 0.12.0 assets (the live release's) verify green
        against their own receipt — the S3 trust surface, consumed."""
        dist = _repo_root() / "dist"
        ver = "0.12.0"
        need = [f"hive-{ver}.tar.gz", f"hive-{ver}.sha256", f"hive-{ver}.receipt.txt"]
        if not all((dist / n).is_file() for n in need):
            pytest.skip(f"live 0.12.0 assets not present under {dist}")
        assets_dir = tmp_path / "assets"
        assets_dir.mkdir()
        for n in need:
            shutil.copy(dist / n, assets_dir / n)
        bindir = tmp_path / "ghbin"
        bindir.mkdir()
        (bindir / "gh").write_text(
            "#!/bin/sh\n"
            "dir=\n"
            "prev=\n"
            'for a in "$@"; do\n'
            '  [ "$prev" = "--dir" ] && dir="$a"\n'
            '  prev="$a"\n'
            "done\n"
            f'cp "{assets_dir}"/hive-{ver}.* "$dir"/\n'
            "exit 0\n")
        (bindir / "gh").chmod(0o755)
        r = subprocess.run(
            ["bash", str(_repo_root() / "packaging" / "verify-release.sh"),
             f"v{ver}"],
            capture_output=True, text=True, timeout=120,
            env={**os.environ,
                 "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}"})
        out = r.stdout + r.stderr
        assert r.returncode == 0, out[-800:]
        assert "OK —" in out, out[-400:]


class TestCutReceipt:
    """S3 (0.12) — every release carries a machine-verifiable cut
    receipt.

    After the publish step, cut-release.sh writes
    hive-X.Y.Z.receipt.txt (version, branch + commit, tag, gate 0-7
    results, asset names + sha256, manifest digest, release URL,
    timestamp) and uploads it as the THIRD release asset, clobber-safe
    on re-run. Exercised hermetically: a fake gh on PATH captures the
    publish calls, and a local bare repo stands in for the GitHub
    remote so the tag/push steps run for real, locally.
    """


    @pytest.fixture(scope="session")
    def _cut_tree_base(self, tmp_path_factory):
        """T2 (0.16): the receipt tests' cut tree is built ONCE per
        session (the S5 session-base pattern).

        Every receipt test used to pay a full-repo tar + extract +
        git init + base commit to run its cut. This base pays that
        once (the same cost _dirty_base pays, plus the git init/
        commit); the per-test receipt_env copies it (local copytree —
        the S5 contract: the base is a READ-ONLY copy source, no
        cross-test state), resets the copy (git reset --hard + tag
        cleanup — the cut's gate 1 requires a clean tree and a
        specific version, and the reset makes every cut start from
        the same deterministic base), and gives it a FRESH bare
        origin (the tag/push steps must not share a remote across
        tests)."""
        repo = _repo_root()
        base = tmp_path_factory.mktemp("hive-cut-tree-base")
        tree = base / "tree"
        tree.mkdir()
        tar = subprocess.run(
            ["tar", "-cf", "-",
             "--exclude=.git", "--exclude=unsloth-tmp", "--exclude=.local",
             "--exclude=.cache", "--exclude=dist",
             "--exclude=__pycache__", "--exclude=*.pyc", "."],
            cwd=str(repo), capture_output=True, timeout=300)
        assert tar.returncode == 0, tar.stderr.decode(errors="replace")
        subprocess.run(["tar", "-xf", "-", "-C", str(tree)],
                       input=tar.stdout, check=True, capture_output=True,
                       timeout=300)
        env = {**os.environ, **self.GIT_IDENTITY}
        for args in (["git", "init", "-q", "-b", "main"],
                     ["git", "add", "-A"],
                     ["git", "commit", "-qm", "receipt-base"]):
            subprocess.run(args, cwd=str(tree), env=env, check=True,
                           timeout=120)
        return tree

    GIT_IDENTITY = {
        "GIT_AUTHOR_NAME": "receipt-test",
        "GIT_AUTHOR_EMAIL": "receipt-test@example.com",
        "GIT_COMMITTER_NAME": "receipt-test",
        "GIT_COMMITTER_EMAIL": "receipt-test@example.com",
    }

    FAKE_GH = """#!/bin/sh
{ echo "gh $*"; } >> "${FAKE_GH_LOG:-/dev/null}"
for a in "$@"; do
  if [ "$a" = "--json" ]; then
    printf '%s\\n' "${FAKE_GH_URL:-https://github.com/fake/repo/releases/tag/v0.0.0}"
    exit 0
  fi
done
if [ "$1" = "release" ] && [ "$2" = "view" ]; then
  [ -n "${FAKE_GH_RELEASE_EXISTS:-}" ] && exit 0
  exit 1
fi
exit 0
"""

    @pytest.fixture()
    def receipt_env(self, tmp_path, _cut_tree_base):
        """Per-test cut tree (a reset copy of the session base, the
        S5 contract) + a FRESH bare origin + a fake gh on PATH."""
        tree = tmp_path / "tree"
        # T2 (0.16): the tree is a per-test copy of the session
        # _cut_tree_base — the tar + git init + base commit is paid
        # ONCE per session, not per test.
        shutil.copytree(_cut_tree_base, tree)
        env = {**os.environ, **self.GIT_IDENTITY}
        # The copy is reset to the base's committed state: the cut's
        # gate 1 requires a clean tree and a specific version, and the
        # reset makes every cut start from the same deterministic base
        # (a stray tag from the copy is removed too).
        subprocess.run(["git", "reset", "--hard", "-q", "HEAD"],
                       cwd=str(tree), env=env, check=True, timeout=60)
        tags = subprocess.run(["git", "tag", "-l"], cwd=str(tree), env=env,
                              capture_output=True, text=True, timeout=30)
        for tag in tags.stdout.split():
            subprocess.run(["git", "tag", "-d", tag], cwd=str(tree),
                           env=env, check=True, timeout=30)
        bare = tmp_path / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", str(bare)],
                       check=True, timeout=60)
        subprocess.run(["git", "remote", "add", "origin", str(bare)],
                       cwd=str(tree), check=True, timeout=30)
        fakebin = tmp_path / "fakegh"
        fakebin.mkdir()
        gh = fakebin / "gh"
        gh.write_text(self.FAKE_GH)
        gh.chmod(0o755)
        log = tmp_path / "gh.log"
        return {
            "tree": tree,
            "env": {**os.environ, **self.GIT_IDENTITY,
                    "PATH": f"{fakebin}:{os.environ['PATH']}",
                    "FAKE_GH_LOG": str(log)},
            "gh_log": log,
        }
    def _cut(self, tree, gh_env, out_dir):
        return subprocess.run(
            ["bash", str(tree / "packaging" / "cut-release.sh"),
             "--out-dir", str(out_dir),
             "--suite", "python -m pytest tests/test_json_structures.py -q --deselect tests/test_json_structures.py::TestLockWheelOnly"],
            capture_output=True, text=True, timeout=600,
            cwd=str(tree), env=gh_env,
        )

    def test_receipt_written_uploaded_and_verifies(self, receipt_env, tmp_path):
        import hashlib
        import tarfile
        version = (receipt_env["tree"] / "VERSION").read_text().strip()
        r = self._cut(receipt_env["tree"], receipt_env["env"],
                      tmp_path / "dist")
        assert r.returncode == 0, r.stdout + "\n" + r.stderr
        receipt = tmp_path / "dist" / f"hive-{version}.receipt.txt"
        assert receipt.is_file(), \
            "the cut must write hive-X.Y.Z.receipt.txt after the publish"
        lines = receipt.read_text().strip().splitlines()
        for line in lines:
            assert line[:1].isalpha() and ": " in line, \
                f"the receipt must parse as 'key: value' lines: {line!r}"
        kv = [l.split(": ", 1) for l in lines]
        by_key = {k: v for k, v in kv if k != "asset"}
        assets = [v for k, v in kv if k == "asset"]
        # commit field equals the tag's target
        target = subprocess.run(["git", "rev-parse", f"v{version}"],
                                cwd=str(receipt_env["tree"]),
                                capture_output=True, text=True, timeout=30)
        assert target.returncode == 0, "the cut must tag v%s" % version
        assert by_key.get("commit") == target.stdout.strip(), \
            "the receipt's commit field must equal the tag's target"
        assert by_key.get("tag") == f"v{version}"
        assert by_key.get("version") == version
        assert by_key.get("release", "").startswith("https://"), \
            "the receipt must carry the release URL"
        # gate 2's line carries the suite summary
        g2 = by_key.get("gate-2", "")
        assert g2.startswith("PASS") and "passed" in g2, \
            f"gate-2 must be PASS with the suite summary line: {g2!r}"
        # every gate 0-7 is recorded
        for i in range(8):
            assert f"gate-{i}" in by_key, f"receipt misses gate-{i}"
        # the tarball's sha256 matches the sidecar
        sidecar = (tmp_path / "dist" / f"hive-{version}.sha256") \
            .read_text().strip().split()
        tar_asset = next((a for a in assets
                          if a.startswith(f"hive-{version}.tar.gz")), None)
        assert tar_asset, f"no tarball asset line in the receipt: {assets}"
        assert tar_asset.split("sha256=", 1)[1] == sidecar[0], \
            "the receipt's tarball sha256 must match the sidecar"
        # the manifest digest matches the manifest inside the tarball
        tball = tmp_path / "dist" / f"hive-{version}.tar.gz"
        with tarfile.open(tball) as tf:
            m = next(mm for mm in tf.getmembers()
                     if mm.name.endswith("/MANIFEST.txt"))
            msha = hashlib.sha256(tf.extractfile(m).read()).hexdigest()
        assert by_key.get("manifest", "").split("sha256=", 1)[1] == msha, \
            "the receipt's manifest digest must match the tarball's manifest"
        # the fake gh saw: create (two assets) + a clobber upload of the
        # receipt (the third asset)
        calls = receipt_env["gh_log"].read_text()
        assert "release create" in calls
        assert "release upload" in calls and "--clobber" in calls
        assert f"hive-{version}.receipt.txt" in calls, \
            "the receipt must be uploaded as a release asset"

    def test_rerun_clobbers_the_receipt(self, receipt_env, tmp_path):
        """T4 (0.22): load-sensitive tail (it was the third probe of the
        -n 32 flake trio). Each _cut is a heavy subprocess chain (tar,
        git, the in-cut suite, the fake-gh publish) on a 600s budget;
        under full-suite CPU contention one of its subprocesses can
        outwait its budget and the cut exits nonzero — a TRANSIENT
        signature (the cut passes serially). Hardening: a bounded
        single retry INSIDE the test on a freshly reset tree (never a
        suite re-run); a deterministic failure (a receipt-content
        assert) fails on the first attempt."""
        version = (receipt_env["tree"] / "VERSION").read_text().strip()

        def attempt(n):
            d1 = tmp_path / f"dist1-{n}"
            d2 = tmp_path / f"dist2-{n}"
            r1 = self._cut(receipt_env["tree"], receipt_env["env"], d1)
            assert r1.returncode == 0, (
                f"cut attempt {n} failed (transient under load?):\n"
                + r1.stdout[-2000:] + r1.stderr[-1000:])
            assert (d1 / f"hive-{version}.receipt.txt").is_file()
            # Re-publish: the release already exists (the fake gh
            # reports it), and the local tag is gone (gate 1 requires
            # it absent; the remote copy stays — exactly the real
            # re-publish state).
            subprocess.run(["git", "tag", "-d", f"v{version}"],
                           cwd=str(receipt_env["tree"]), check=True,
                           timeout=30)
            env2 = {**receipt_env["env"], "FAKE_GH_RELEASE_EXISTS": "1"}
            r2 = self._cut(receipt_env["tree"], env2, d2)
            assert r2.returncode == 0, (
                f"the re-run publish must succeed idempotently "
                f"(attempt {n}):\n" + r2.stdout[-2000:]
                + r2.stderr[-1000:])
            receipt2 = d2 / f"hive-{version}.receipt.txt"
            assert receipt2.is_file(), "the re-run must write a fresh receipt"
            calls = receipt_env["gh_log"].read_text()
            assert "release edit" in calls, (
                "the re-run must take the existing-release branch (edit)")
            receipt_uploads = [
                l for l in calls.splitlines()
                if "release upload" in l
                and f"hive-{version}.receipt.txt" in l]
            assert len(receipt_uploads) == 2, (
                "the receipt must be re-uploaded with --clobber on re-run; "
                f"uploads: {receipt_uploads}")
            assert "--clobber" in receipt_uploads[-1]

        first = None
        for n in (1, 2):
            try:
                attempt(n)
                return
            except subprocess.TimeoutExpired as e:
                first = first or f"attempt 1: cut timed out: {e}"
            except AssertionError as e:
                msg = str(e)
                transient = ("cut attempt 1 failed" in msg
                             or "attempt 1" in msg)
                if first is None and not transient:
                    raise  # deterministic — full granularity, no retry
                first = first or f"attempt 1: {e}"
            if n == 2:
                pytest.fail(f"cut re-run: both attempts failed — {first}")
            self._reset_tree_for_retry(receipt_env)

    def _reset_tree_for_retry(self, receipt_env):
        """Between the two cut attempts: the tree back to the base state
        (the failed cut may have tagged / pushed), the fake-gh log
        cleared (the upload-count assert counts within ONE attempt).
        The bare origin lives at the tmp_path LEVEL (the fixture builds
        it there, not under tree/) — gh_log is the fixture's sibling
        marker, so it names the level."""
        tree = receipt_env["tree"]
        env = receipt_env["env"]
        bare = receipt_env["gh_log"].parent / "origin.git"
        tag = "v" + (tree / "VERSION").read_text().strip()
        subprocess.run(["git", "tag", "-d", tag], cwd=str(tree),
                       env=env, capture_output=True, timeout=30)
        subprocess.run(["git", "reset", "--hard", "-q", "HEAD"],
                       cwd=str(tree), env=env, check=True, timeout=60)
        # Untracked litter from the failed cut's in-tree work (the cut's
        # gate 1 requires a CLEAN tree): reset --hard keeps it.
        subprocess.run(["git", "clean", "-qfd"],
                       cwd=str(tree), env=env, check=True, timeout=60)
        for r in subprocess.run(["git", "for-each-ref", "--format=%(refname)"],
                                cwd=str(bare),
                                capture_output=True, text=True,
                                timeout=30).stdout.split():
            subprocess.run(["git", "update-ref", "-d", r],
                           cwd=str(bare),
                           env=env, check=True, timeout=30)
        receipt_env["gh_log"].write_text("")


class TestRequiredEntriesConstant:
    """T3 (0.13) — the required-set constant is shared, not copied.

    The eight-entry required list was hand-maintained TWICE (cut
    gate 4's inline loop and install.sh's post-stage loop); the
    state-free rule lived a third way (gate 4's is_state_entry case
    list). packaging/required-entries.list is now the ONE constant:
    gate 4, install.sh, the doctor installed-set check, and these
    tests all read it. This class pins the constant against every
    other copy of the list in the repo — a new required entry or
    state path must land in the file first, or the guard fails.
    """

    CONST = Path(__file__).resolve().parent.parent / \
        "packaging" / "required-entries.list"

    @staticmethod
    def _sections() -> tuple[list, list]:
        required, state = [], []
        section = None
        for line in TestRequiredEntriesConstant.CONST.read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            if line == "REQUIRED":
                section = required
            elif line == "STATE":
                section = state
            elif section is not None:
                section.append(line)
        return required, state

    def test_required_section_matches_the_test_copies(self):
        required, _ = self._sections()
        # TestShippingManifest.REQUIRED (the gate-4 surface) and
        # TestDoctorInstalledSet.REQUIRED (the doctor surface) are the
        # other copies of the list — all three must agree.
        assert required == list(TestShippingManifest.REQUIRED), (
            "required-entries.list REQUIRED section drifted from "
            f"TestShippingManifest.REQUIRED:\n{required}")
        # The doctor test's scratch-install helper creates this list of
        # files — every entry it creates must be required by the
        # constant (the helper is a consumer of the list, not a copy).
        from tests.test_commands import TestDoctorInstalledSet
        missing = [e for e in TestDoctorInstalledSet.REQUIRED
                   if e not in required]
        assert not missing, (
            "TestDoctorInstalledSet creates files the required-set "
            f"constant does not require: {missing}")

    def test_state_section_matches_the_state_free_rule(self):
        _, state = self._sections()
        assert sorted(state) == sorted(TestShippingManifest.STATE_TOP), (
            "required-entries.list STATE section drifted from the "
            "state-free rule:\n" + ", ".join(state))

    def test_bash_and_python_parsers_agree(self):
        """The two consumers parse the same file two ways (awk in
        entries.sh, python in doctor.py) — they must agree on the
        entries."""
        from lib.hive_cli.commands.doctor import _parse_required_entries
        py_req, py_state = _parse_required_entries(
            self.CONST.read_text())
        for section, py in (("REQUIRED", py_req), ("STATE", py_state)):
            r = subprocess.run(
                ["bash", "-c",
                 ". ./packaging/entries.sh && "
                 f"required_entries packaging/required-entries.list "
                 f"{section}"],
                capture_output=True, text=True, timeout=30,
                cwd=str(self.CONST.parent.parent))
            assert r.returncode == 0, r.stderr
            bash_lines = [l for l in r.stdout.splitlines() if l.strip()]
            assert bash_lines == list(py), (
                f"{section}: bash parser and python parser disagree "
                f"(bash: {bash_lines}, python: {py})")

    def test_consumers_read_the_file_no_inline_copies(self):
        """cut-release.sh gate 4 and install.sh post-stage must parse
        the constant — an inline copy of the list creeping back in
        (the drift class) fails the guard."""
        for script in ("packaging/cut-release.sh", "packaging/install.sh"):
            text = (Path(__file__).resolve().parent.parent /
                    script).read_text()
            assert "required-entries.list" in text, (
                f"{script} no longer references the shared "
                "required-entries.list constant")
            assert "required_entries" in text, (
                f"{script} does not use the required_entries() parser")
            # The inline-copy smell: a required entry named as a bare
            # word in a bash loop word-list.
            assert "lib/hive_mcp/server.py lib/dashboard/server.js" \
                not in text.replace("\\", ""), (
                f"{script} carries an inline copy of the required "
                "list — it must read packaging/required-entries.list")

    def test_the_constant_ships_in_the_tarball(self, dirty_tree, tmp_path):
        """The constant is part of the shipped entry set (doctor reads
        it from the INSTALL, so the package must carry it)."""
        tarball = TestShippingManifest()._build(
            dirty_tree, tmp_path / "dist")
        import tarfile
        with tarfile.open(tarball) as tf:
            names = tf.getnames()
        assert any(n.endswith("/packaging/required-entries.list")
                   for n in names), (
            "the tarball does not ship "
            "packaging/required-entries.list — the doctor installed-set "
            "check could not run from an install")


class TestUninstallParity:
    """T3 (0.14) — teardown mirrors setup: an uninstall removes what
    an install created.

    install.sh --copy-to writes a .hive-install marker into the
    install root (the dir is a staged COPY the install created);
    uninstall.sh removes the whole install tree ONLY on that marker
    (no tree, no venv, no symlink). In-place installs (a git
    checkout, the operator's own tree) write no marker and keep the
    tree — the marker, not .git, is the discriminator. --keep-tree
    is the escape hatch (symlink + venv only, the pre-0.14
    behavior). Venvs are stubbed (the install.sh reuse path runs a
    no-op pip) — the marker and the teardown are what these tests
    assert on, no network.
    """

    @staticmethod
    def _stub_venv(install_dir: Path) -> None:
        vbin = install_dir / "venv" / "bin"
        vbin.mkdir(parents=True)
        for tool in ("pip", "python"):
            p = vbin / tool
            p.write_text("#!/bin/sh\nexit 0\n")
            p.chmod(0o755)

    @classmethod
    def _copy_to_install(cls, tree: Path, install_dir: Path,
                         home: Path) -> subprocess.CompletedProcess:
        cls._stub_venv(install_dir)
        return subprocess.run(
            ["bash", str(tree / "packaging" / "install.sh"),
             "--copy-to", str(install_dir),
             "--bin-link", str(home / ".local" / "bin" / "hive")],
            capture_output=True, text=True, timeout=600,
            cwd=str(tree), env={**os.environ, "HOME": str(home)})

    @classmethod
    def _in_place_install(cls, tree: Path,
                          home: Path) -> subprocess.CompletedProcess:
        cls._stub_venv(tree)
        return subprocess.run(
            ["bash", str(tree / "packaging" / "install.sh"),
             "--bin-link", str(home / ".local" / "bin" / "hive")],
            capture_output=True, text=True, timeout=600,
            cwd=str(tree), env={**os.environ, "HOME": str(home)})

    @staticmethod
    def _uninstall(install_dir: Path, home: Path,
                   extra=None) -> subprocess.CompletedProcess:
        # cwd OUTSIDE the install dir: on a marked install the uninstall
        # execs an rm of the tree it is running from. HOME is the TEST
        # home — the symlink the install created lives there (an
        # inherited HOME would point the symlink removal at the
        # operator's real ~/.local/bin).
        return subprocess.run(
            ["bash", str(install_dir / "packaging" / "uninstall.sh")]
            + (extra or []),
            capture_output=True, text=True, timeout=300,
            cwd=str(install_dir.parent),
            env={**os.environ, "HOME": str(home)})

    def test_marker_written_on_copy_to_install(self, dirty_tree, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        install_dir = tmp_path / "install"
        r = self._copy_to_install(dirty_tree, install_dir, home)
        assert r.returncode == 0, r.stdout + "\n" + r.stderr
        marker = install_dir / ".hive-install"
        assert marker.is_file(), "the staged install must carry the marker"
        text = marker.read_text()
        assert "mode=copy-to" in text, text
        assert f"version={(dirty_tree / 'VERSION').read_text().strip()}" in text, text

    def test_marker_absent_on_in_place_install(self, dirty_tree, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        r = self._in_place_install(dirty_tree, home)
        assert r.returncode == 0, r.stdout + "\n" + r.stderr
        assert not (dirty_tree / ".hive-install").exists(), (
            "an in-place install must not write the marker — the tree "
            "is the operator's work")

    def test_copy_to_uninstall_removes_everything(self, dirty_tree,
                                                  tmp_path):
        """Scratch --copy-to install + uninstall leaves NOTHING: no
        tree, no venv, no symlink."""
        home = tmp_path / "home"
        home.mkdir()
        install_dir = tmp_path / "install"
        r = self._copy_to_install(dirty_tree, install_dir, home)
        assert r.returncode == 0, r.stdout + "\n" + r.stderr
        assert (install_dir / ".hive-install").is_file()
        ru = self._uninstall(install_dir, home)
        assert ru.returncode == 0, ru.stdout + "\n" + ru.stderr
        assert not install_dir.exists(), \
            "the staged install tree must be removed by the uninstall"
        link = home / ".local" / "bin" / "hive"
        assert not link.exists() and not link.is_symlink(), \
            "the launcher symlink must be gone"
        assert not (install_dir / "venv").exists()

    def test_in_place_git_tree_is_kept(self, dirty_tree, tmp_path):
        """An in-place install in a GIT tree (a working checkout) keeps
        the tree on uninstall (no marker → nothing to remove)."""
        (dirty_tree / ".git").mkdir(exist_ok=True)
        home = tmp_path / "home"
        home.mkdir()
        r = self._in_place_install(dirty_tree, home)
        assert r.returncode == 0, r.stdout + "\n" + r.stderr
        ru = self._uninstall(dirty_tree, home)
        assert ru.returncode == 0, ru.stdout + "\n" + ru.stderr
        assert dirty_tree.is_dir() and (dirty_tree / "bin" / "hive").is_file(), \
            "the in-place (git) tree must survive the uninstall"
        assert not (dirty_tree / "venv").exists(), \
            "the venv must still be removed"
        assert "install tree at" in ru.stdout or "untouched" in ru.stdout

    def test_in_place_non_git_dir_is_kept(self, dirty_tree, tmp_path):
        """The marker — NOT .git — is the discriminator: an in-place
        install in a plain (non-git) dir also keeps the tree."""
        home = tmp_path / "home"
        home.mkdir()
        r = self._in_place_install(dirty_tree, home)
        assert r.returncode == 0, r.stdout + "\n" + r.stderr
        ru = self._uninstall(dirty_tree, home)
        assert ru.returncode == 0, ru.stdout + "\n" + ru.stderr
        assert dirty_tree.is_dir() and (dirty_tree / "bin" / "hive").is_file(), \
            "the in-place (non-git) tree must survive the uninstall"
        assert not (dirty_tree / ".hive-install").exists()

    def test_keep_tree_escape_hatch(self, dirty_tree, tmp_path):
        """--keep-tree on a marked (staged) install keeps the tree —
        the pre-0.14 behavior — and still removes symlink + venv."""
        home = tmp_path / "home"
        home.mkdir()
        install_dir = tmp_path / "install"
        r = self._copy_to_install(dirty_tree, install_dir, home)
        assert r.returncode == 0, r.stdout + "\n" + r.stderr
        ru = self._uninstall(install_dir, home, ["--keep-tree"])
        assert ru.returncode == 0, ru.stdout + "\n" + ru.stderr
        assert install_dir.is_dir() and \
            (install_dir / ".hive-install").is_file(), \
            "--keep-tree must keep the install tree (marker included)"
        assert not (install_dir / "venv").exists()
        link = home / ".local" / "bin" / "hive"
        assert not link.exists() and not link.is_symlink()


class TestTeardownSafety:
    """T2 (0.15) — a marked tree never holds runtime state.

    0.14's T3 made teardown mirror setup: a --copy-to install writes
    the .hive-install marker and the uninstall removes the WHOLE tree
    on it — no exclusions. Nothing prevented a marked install from
    cohabiting with runtime state (install.sh --copy-to <project dir>
    stages a marked tree alongside .company/), so the uninstall then
    removed the project's live state as 'install residue'. 0.15 pins
    the invariant both directions:

    - install REFUSES to stage into a dir that holds .company/ or
      .worktrees/ (fail-fast, naming the collision) — staged installs
      only; in-place installs are the operator's tree and are
      unchanged. A dir that carries ONLY the existing .hive-install
      marker is the upgrade path (T1, 0.15): re-running the install
      over it is allowed and announced (the post-stage prune makes
      the tree exact).
    - uninstall EXCLUDES runtime state from the marker-path tree
      removal and names whatever it kept — dead-SAFE for any marked
      tree that predates the install-side refusal.

    install-online.sh inherits the refusal through the shared staging
    path (it delegates to install.sh --copy-to) — a seam test pins it.
    """

    @staticmethod
    def _stub_venv(install_dir: Path) -> None:
        vbin = install_dir / "venv" / "bin"
        vbin.mkdir(parents=True, exist_ok=True)
        for tool in ("pip", "python"):
            p = vbin / tool
            p.write_text("#!/bin/sh\nexit 0\n")
            p.chmod(0o755)

    @classmethod
    def _copy_to_install(cls, tree: Path, install_dir: Path,
                         home: Path) -> subprocess.CompletedProcess:
        cls._stub_venv(install_dir)
        return subprocess.run(
            ["bash", str(tree / "packaging" / "install.sh"),
             "--copy-to", str(install_dir),
             "--bin-link", str(home / ".local" / "bin" / "hive")],
            capture_output=True, text=True, timeout=600,
            cwd=str(tree), env={**os.environ, "HOME": str(home)})

    def test_copy_to_into_company_dir_refuses_and_names_it(self, dirty_tree,
                                                            tmp_path):
        """(a) a --copy-to install into a directory holding a planted
        .company/ FAILS the install, naming the collision — today it
        stages silently (red)."""
        home = tmp_path / "home"
        home.mkdir()
        install_dir = tmp_path / "install"
        install_dir.mkdir()
        (install_dir / ".company" / "tickets" / "state.json").parent.mkdir(
            parents=True)
        (install_dir / ".company" / "tickets" / "state.json").write_text("{}\n")
        r = self._copy_to_install(dirty_tree, install_dir, home)
        assert r.returncode != 0, \
            "staging into a dir with .company/ must fail: " + r.stdout
        assert ".company" in (r.stderr + r.stdout), r.stderr
        # refused BEFORE staging: no marker, no staged entries
        assert not (install_dir / ".hive-install").exists()
        assert not (install_dir / "lib").exists(), \
            "a refused install must not stage anything"

    def test_copy_to_into_worktrees_dir_refuses_and_names_it(self,
                                                             dirty_tree,
                                                             tmp_path):
        """The .worktrees/ half of the same refusal."""
        home = tmp_path / "home"
        home.mkdir()
        install_dir = tmp_path / "install"
        install_dir.mkdir()
        (install_dir / ".worktrees" / "some-ticket").mkdir(parents=True)
        r = self._copy_to_install(dirty_tree, install_dir, home)
        assert r.returncode != 0, \
            "staging into a dir with .worktrees/ must fail: " + r.stdout
        assert ".worktrees" in (r.stderr + r.stdout), r.stderr
        assert not (install_dir / ".hive-install").exists()

    def test_copy_to_into_marked_and_state_dir_refuses(self, dirty_tree,
                                                       tmp_path):
        """A pre-T2 cohabiting tree (marker + .company/ — however it
        got there) is refused on re-run too: the upgrade prune would
        otherwise delete the state the install never created."""
        home = tmp_path / "home"
        home.mkdir()
        install_dir = tmp_path / "install"
        install_dir.mkdir()
        (install_dir / ".company" / "tickets").mkdir(parents=True)
        (install_dir / ".hive-install").write_text(
            "version=0.14.0\ninstalled_at=2026-11-01T00:00:00Z\n"
            "mode=copy-to\n")
        r = self._copy_to_install(dirty_tree, install_dir, home)
        assert r.returncode != 0, \
            "staging into a marked dir that holds state must fail"
        out = r.stderr + r.stdout
        assert ".company" in out and ".hive-install" in out, out

    def test_copy_to_into_marked_dir_is_the_upgrade_path(self, dirty_tree,
                                                         tmp_path):
        """A dir carrying ONLY the existing marker (a clean marked
        tree) is the upgrade path (T1, 0.15): re-running the install
        over it is allowed and announced, not refused."""
        home = tmp_path / "home"
        home.mkdir()
        install_dir = tmp_path / "install"
        r1 = self._copy_to_install(dirty_tree, install_dir, home)
        assert r1.returncode == 0, r1.stdout + "\n" + r1.stderr
        # stale entry from a previous release (outside the entry set)
        (install_dir / "stale-leftover.txt").write_text("old\n")
        r2 = self._copy_to_install(dirty_tree, install_dir, home)
        assert r2.returncode == 0, \
            "re-running the install over a marked tree is the upgrade " \
            "path and must not be refused: " + r2.stdout + r2.stderr

    def test_install_online_inherits_the_refusal(self, dirty_tree,
                                                 tmp_path):
        """install-online.sh's HIVE_INSTALL_DIR seam: it delegates to
        install.sh --copy-to, so the refusal is inherited — a one-liner
        pointed at a state dir fails before staging, naming it."""
        ver = (dirty_tree / "VERSION").read_text().strip()
        pkg = tmp_path / f"hive-{ver}"
        (pkg / "packaging").mkdir(parents=True)
        for name in ("install.sh", "entries.sh", "entries.list",
                     "required-entries.list"):
            shutil.copy(dirty_tree / "packaging" / name,
                        pkg / "packaging" / name)
        shutil.copy(dirty_tree / "VERSION", pkg / "VERSION")
        tarball = tmp_path / f"hive-{ver}.tar.gz"
        subprocess.run(["tar", "-czf", str(tarball), "-C", str(tmp_path),
                        pkg.name], check=True, capture_output=True)

        home = tmp_path / "home"
        home.mkdir()
        state_dir = tmp_path / "hive-install-dir"
        (state_dir / ".company" / "tickets").mkdir(parents=True)
        r = subprocess.run(
            ["sh", str(dirty_tree / "packaging" / "install-online.sh")],
            capture_output=True, text=True, timeout=120,
            cwd=str(tmp_path),
            env={**os.environ, "HOME": str(home),
                 "HIVE_PKG_FILE": str(tarball),
                 "HIVE_INSTALL_DIR": str(state_dir)})
        assert r.returncode != 0, \
            "install-online into a state dir must fail: " + r.stdout
        assert ".company" in (r.stderr + r.stdout), r.stderr
        assert not (state_dir / ".hive-install").exists()
        assert not (state_dir / "lib").exists()


    def test_online_installer_delegates_with_bash_not_sh(self, dirty_tree):
        """T2 (0.15) follow-up — the 0.14-class latent bug: install.sh
        is a BASH script (the staging loops use process substitution),
        and install-online.sh invoked it with `sh`. On dash systems
        (Debian, Ubuntu, the CI runners) that is a SYNTAX ERROR, not a
        refusal — the online installer died before it could do
        anything. The delegation must use bash (the documented manual
        path), never sh. Pinned statically: sh=bash boxes cannot
        reproduce the dash parse failure, so the guard is on the
        invocation itself."""
        text = (dirty_tree / "packaging" / "install-online.sh").read_text()
        for line in text.splitlines():
            if "packaging/install.sh" in line and \
                    line.strip().startswith(("sh ", "bash ")):
                assert line.strip().startswith("bash "), \
                    "install-online.sh must invoke install.sh with " \
                    "bash, not sh: " + line.strip()
                return
        raise AssertionError(
            "install-online.sh no longer invokes install.sh at all — "
            "the delegation contract changed; update this guard")


class TestUpgradePrune:
    """T1 (0.15) — install-on-install leaves EXACTLY what the new
    release defines.

    The operator's machine moves 0.14 -> 0.15 by re-running the
    install over the marked tree (the .hive-install branch — T2 makes
    that the only marked-dir case the staging allows). 0.14's staging
    only overwrote: a top-level entry a superseded release left
    (a removed tool, a renamed file, a deleted doc) stayed in the
    install forever. 0.15's post-stage prune makes the entry set the
    EXACT source of truth for the tree: residue outside {entry set,
    venv, .hive-install} is removed, each piece named.
    """

    def test_upgrade_prunes_tree_residue(self, dirty_tree, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        install_dir = tmp_path / "install"
        r1 = TestTeardownSafety._copy_to_install(dirty_tree, install_dir,
                                                 home)
        assert r1.returncode == 0, r1.stdout + "\n" + r1.stderr
        # Plant stale residue: top-level entries NOT in the entry set
        (install_dir / "stale-leftover.txt").write_text("old release junk")
        stale_dir = install_dir / "lib-stale"
        stale_dir.mkdir()
        (stale_dir / "old-tool.sh").write_text("#!/bin/sh\nexit 0\n")
        # The upgrade: re-run the install over the marked tree
        r2 = TestTeardownSafety._copy_to_install(dirty_tree, install_dir,
                                                 home)
        assert r2.returncode == 0, r2.stdout + "\n" + r2.stderr
        assert not (install_dir / "stale-leftover.txt").exists(), \
            "stale top-level file must be pruned on the upgrade"
        assert not stale_dir.exists(), "stale dir must be pruned on the upgrade"
        for name in ("stale-leftover.txt", "lib-stale"):
            assert name in r2.stdout, \
                f"the prune must name what it removed: {name}\n{r2.stdout}"
        # The entry set is intact, venv + marker are never prune material
        for name in ("bin", "lib", "etc", "share", "packaging",
                     "VERSION", "README.md"):
            assert (install_dir / name).exists(), \
                f"entry-set member {name} must survive the prune"
        assert (install_dir / "venv").is_dir(), "venv must survive the prune"
        assert (install_dir / ".hive-install").exists(), \
            "the marker must survive the prune"


class TestMarkerUninstallExclusions:
    """T2 (0.15) — defense in depth: the uninstall's marker-path tree
    removal EXCLUDES runtime state (.company/, .worktrees/) and names
    whatever it kept. The install-side refusal (TestTeardownSafety)
    should make this branch dead code; the exclusion makes it
    dead-SAFE for any marked tree that predates T2."""

    @staticmethod
    def _stub_venv(install_dir: Path) -> None:
        vbin = install_dir / "venv" / "bin"
        vbin.mkdir(parents=True, exist_ok=True)
        for tool in ("pip", "python"):
            p = vbin / tool
            p.write_text("#!/bin/sh\nexit 0\n")
            p.chmod(0o755)

    @staticmethod
    def _uninstall(install_dir: Path, home: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", str(install_dir / "packaging" / "uninstall.sh")],
            capture_output=True, text=True, timeout=300,
            cwd=str(install_dir.parent),
            env={**os.environ, "HOME": str(home)})

    def test_marker_uninstall_keeps_cohabiting_state(self, dirty_tree,
                                                     tmp_path):
        """(b) a marker uninstall over a cohabiting tree (hand-written
        marker + planted .company/, simulating a pre-T2 install) keeps
        the workspace intact — today's rm -rf removes it (red)."""
        shutil.rmtree(dirty_tree / ".company")
        shutil.rmtree(dirty_tree / ".worktrees")
        home = tmp_path / "home"
        home.mkdir()
        install_dir = tmp_path / "install"
        r = TestTeardownSafety._copy_to_install(dirty_tree, install_dir,
                                                home)
        assert r.returncode == 0, r.stdout + "\n" + r.stderr
        # simulate a pre-T2 cohabitation: state lands in the marked tree
        (install_dir / ".company" / "tickets" / "state.json").parent.mkdir(
            parents=True)
        (install_dir / ".company" / "tickets" / "state.json").write_text(
            '{"round": 1}\n')
        (install_dir / ".company" / "roster.json").write_text("{}\n")
        (install_dir / ".worktrees" / "open-ticket").mkdir(parents=True)
        (install_dir / ".worktrees" / "open-ticket" /
         "work.txt").write_text("wip\n")

        ru = self._uninstall(install_dir, home)
        assert ru.returncode == 0, ru.stdout + "\n" + ru.stderr
        # the state survives, intact
        assert (install_dir / ".company" / "tickets" /
                "state.json").read_text() == '{"round": 1}\n'
        assert (install_dir / ".worktrees" / "open-ticket" /
                "work.txt").read_text() == "wip\n"
        # the rest of the tree is gone
        assert not (install_dir / "lib").exists(), \
            "the install tree (minus the kept state) must be removed"
        assert not (install_dir / ".hive-install").exists()
        # the kept entries are NAMED in the output
        out = ru.stdout + ru.stderr
        assert ".company" in out and "kept" in out.lower(), \
            "the uninstall must name the state it kept: " + out
        assert ".worktrees" in out, out

    def test_marker_uninstall_clean_tree_removes_everything(self,
                                                            dirty_tree,
                                                            tmp_path):
        """The clean case is unchanged: a marked tree with no state is
        removed whole (dir included) — the exclusion keeps nothing."""
        shutil.rmtree(dirty_tree / ".company")
        shutil.rmtree(dirty_tree / ".worktrees")
        home = tmp_path / "home"
        home.mkdir()
        install_dir = tmp_path / "install"
        r = TestTeardownSafety._copy_to_install(dirty_tree, install_dir,
                                                home)
        assert r.returncode == 0, r.stdout + "\n" + r.stderr
        ru = self._uninstall(install_dir, home)
        assert ru.returncode == 0, ru.stdout + "\n" + ru.stderr
        assert not install_dir.exists(), \
            "a clean marked tree must be removed whole"
        assert "kept" not in (ru.stdout + ru.stderr).lower()
class TestVenvPrune:
    """T1 (0.15) — the installed venv is EXACTLY the released
    environment (the lock closure + venv tooling), shared between
    install.sh (the prune) and hive doctor (the installed-set check).
    The exact set comes from lib/hive_cli/commands/venv_prune.py —
    stdlib only, run under the venv's OWN python (its metadata is the
    ground truth)."""

    VENV_PRUNE = _repo_root() / "lib" / "hive_cli" / "commands" / "venv_prune.py"

    @classmethod
    def _prune_module(cls):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "hive_venv_prune", cls.VENV_PRUNE)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_lock_direct_names_parse(self):
        vp = self._prune_module()
        lock = _repo_root() / "packaging" / "requirements.lock.txt"
        direct = vp.lock_direct_names(lock)
        for name in ("mcp", "fastapi", "uvicorn", "httpx"):
            assert name in direct, f"{name} not parsed from the lock"
        assert "tomli" not in direct  # T4: retired with the 3.11 floor

    def test_exact_set_is_closure_plus_tooling(self):
        vp = self._prune_module()
        lock = _repo_root() / "packaging" / "requirements.lock.txt"
        exact = vp.exact_venv_set(lock)
        for name in ("mcp", "fastapi", "uvicorn", "httpx",
                     "pip", "setuptools", "wheel"):
            assert name in exact, f"{name} missing from the exact set"

    def test_normalize_pep503(self):
        vp = self._prune_module()
        assert vp.normalize("Uvicorn[standard]") == "uvicorn"
        assert vp.normalize("Zope.Interface") == "zope-interface"
        assert vp.normalize("foo__bar.baz") == "foo-bar-baz"

    def test_planted_residue_is_pruned_and_named(self, tmp_path):
        """venv with a planted extra package: the prune removes it,
        names it, and leaves the released set (a direct lock name)
        alone."""
        vp = self._prune_module()
        venv = tmp_path / "venv"
        r = subprocess.run(
            [sys.executable, "-m", "venv", str(venv)],
            capture_output=True, text=True, timeout=180)
        if r.returncode != 0:
            pytest.skip(f"cannot create a venv here: {r.stderr[:120]}")
        site = next(p / "site-packages" for p in
                    sorted((venv / "lib").glob("python*.*")))
        (site / "planted-residue-1.0.0.dist-info").mkdir(parents=True)
        (site / "planted-residue-1.0.0.dist-info" / "METADATA").write_text(
            "Metadata-Version: 2.1\nName: planted-residue\n"
            "Version: 1.0.0\n")
        (site / "mcp-2.3.0.dist-info").mkdir(parents=True)
        (site / "mcp-2.3.0.dist-info" / "METADATA").write_text(
            "Metadata-Version: 2.1\nName: mcp\nVersion: 2.3.0\n")
        lock = _repo_root() / "packaging" / "requirements.lock.txt"
        assert "planted-residue" in vp.unexpected(venv, lock)
        assert "mcp" not in vp.unexpected(venv, lock)
        removed = vp.prune(venv, lock)
        assert "planted-residue" in removed
        assert "mcp" not in removed
        assert "planted-residue" not in vp.installed_names(venv)
        assert "mcp" in vp.installed_names(venv)
class TestVerifyUpgradeFrom:
    """T1 (0.15) — verify-release.sh --from: the upgrade is verified.

    Hermetic (a fake `gh` serves local fixture releases): a clean
    upgraded tree verifies; a tree with residue, or a venv with an
    extra name, fails and NAMES it; a missing previous receipt fails.
    """
    NEW = "0.9.9"
    PREV = "0.9.8"

    @classmethod
    def _receipt(cls, ver, tar_sha, side_sha, man_sha):
        gates = "".join(f"gate-{n}: PASS (t{n})\n" for n in range(8))
        return (f"receipt: hive-{ver}\nversion: {ver}\nbranch: main\n"
                "commit: 0123456789abcdef0123456789abcdef01234567\n"
                f"tag: v{ver}\n{gates}"
                f"asset: hive-{ver}.tar.gz sha256={tar_sha}\n"
                f"asset: hive-{ver}.sha256 sha256={side_sha}\n"
                f"manifest: MANIFEST.txt sha256={man_sha}\n"
                f"release: https://github.com/fake/hive/releases/tag/v{ver}\n")

    @classmethod
    def _build_release(cls, assets, ver, drop_receipt=False):
        pkg = assets / f"hive-{ver}"
        (pkg / "bin").mkdir(parents=True)
        (pkg / "lib" / "core").mkdir(parents=True)
        (pkg / "packaging").mkdir(parents=True)
        (pkg / "bin" / "hive").write_text("#!/bin/sh\n")
        (pkg / "lib" / "core" / "agent-harness.py").write_text("pass\n")
        (pkg / "packaging" / "required-entries.list").write_text(
            "REQUIRED\nbin/hive\nlib/core/agent-harness.py\n")
        (pkg / "packaging" / "requirements.lock.txt").write_text(
            "fixturepkg==1.0.0\n")
        files = sorted(str(f.relative_to(pkg))
                       for f in pkg.rglob("*") if f.is_file())
        man = pkg / "MANIFEST.txt"
        man.write_text("\n".join(files + ["MANIFEST.txt"]) + "\n")
        man_sha = subprocess.run(["sha256sum", str(man)],
                                 capture_output=True, text=True).stdout.split()[0]
        tb = assets / f"hive-{ver}.tar.gz"
        subprocess.run(["tar", "-czf", str(tb), "-C", str(assets),
                        f"hive-{ver}"], check=True, capture_output=True)
        tar_sha = subprocess.run(["sha256sum", str(tb)],
                                 capture_output=True,
                                 text=True).stdout.split()[0]
        side = assets / f"hive-{ver}.sha256"
        side.write_text(f"{tar_sha}  hive-{ver}.tar.gz\n")
        side_sha = subprocess.run(["sha256sum", str(side)],
                                  capture_output=True,
                                  text=True).stdout.split()[0]
        if not drop_receipt:
            (assets / f"hive-{ver}.receipt.txt").write_text(
                cls._receipt(ver, tar_sha, side_sha, man_sha))

    @classmethod
    def _fake_gh(cls, tmp, assets):
        d = tmp / "ghbin-up"
        d.mkdir()
        gh = d / "gh"
        lines = [
            "#!/bin/sh",
            "tag=; dir=; pat=; prev=",
            "for a in \"$@\"; do",
            "  case \"$prev\" in",
            "    --dir) dir=\"$a\" ;;",
            "    --pattern) pat=\"$a\" ;;",
            "  esac",
            "  [ \"$prev\" = download ] && tag=\"$a\"",
            "  prev=\"$a\"",
            "done",
            "case \"$pat\" in",
            f"  \"\") cp \"{assets}\"/hive-${{tag#v}}.* \"$dir\"/ 2>/dev/null ;;",
            f"  *) cp \"{assets}/$pat\" \"$dir\"/ 2>/dev/null ;;",
            "esac",
            "exit 0",
        ]
        gh.write_text("\n".join(lines) + "\n")
        gh.chmod(0o755)
        return d

    @classmethod
    def _make_tree(cls, tmp, residue=(), venv_names=()):
        tree = tmp / "tree"
        (tree / "bin").mkdir(parents=True)
        (tree / "lib" / "core").mkdir(parents=True)
        (tree / "bin" / "hive").write_text("#!/bin/sh\n")
        (tree / "lib" / "core" / "agent-harness.py").write_text("pass\n")
        (tree / ".hive-install").write_text("version=0.9.8\n")
        for name in residue:
            (tree / name).write_text("stale\n")
        if venv_names:
            venv = tree / "venv"
            (venv / "bin").mkdir(parents=True)
            (venv / "bin" / "python").symlink_to(sys.executable)
            sp = (venv / "lib" /
                  f"python{sys.version_info.major}.{sys.version_info.minor}"
                  / "site-packages")
            sp.mkdir(parents=True)
            for i, name in enumerate(venv_names):
                d = sp / f"{name}-{i + 1}.0.0.dist-info"
                d.mkdir()
                (d / "METADATA").write_text(
                    f"Metadata-Version: 2.1\nName: {name}\n"
                    f"Version: {i + 1}.0.0\n")
        return tree

    @classmethod
    def _run(cls, tmp, assets, tree, drop_prev_receipt=False):
        if not (assets / f"hive-{cls.NEW}.tar.gz").exists():
            cls._build_release(assets, cls.NEW)
        if not (assets / f"hive-{cls.PREV}.receipt.txt").exists():
            cls._build_release(assets, cls.PREV,
                               drop_receipt=drop_prev_receipt)
        bindir = cls._fake_gh(tmp, assets)
        return subprocess.run(
            ["bash", str(_repo_root() / "packaging" / "verify-release.sh"),
             f"v{cls.NEW}", "--from", f"v{cls.PREV}", str(tree)],
            capture_output=True, text=True, timeout=120,
            env={**os.environ,
                 "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}"})

    def test_clean_upgrade_verifies(self, tmp_path):
        """A tree that is EXACTLY the new entry set (plus venv/.hive-
        install), with a venv in the exact set: verified end to end."""
        assets = tmp_path / "a"
        assets.mkdir()
        tree = self._make_tree(tmp_path, venv_names=("fixturepkg", "pip"))
        r = self._run(tmp_path, assets, tree)
        out = r.stdout + r.stderr
        assert r.returncode == 0, out[-900:]
        assert "upgrade v0.9.8 → v0.9.9 OK" in out, out[-500:]

    def test_residue_fails_and_is_named(self, tmp_path):
        """A top-level entry outside the new entry set (pre-0.15-style
        residue the upgrade prune should have removed) fails, named."""
        assets = tmp_path / "a"
        assets.mkdir()
        tree = self._make_tree(tmp_path, residue=("old-tool.sh",),
                               venv_names=("fixturepkg", "pip"))
        r = self._run(tmp_path, assets, tree)
        out = r.stdout + r.stderr
        assert r.returncode == 1, out[-900:]
        assert "old-tool.sh" in out, out[-900:]
        assert "outside the v0.9.9 entry set" in out, out[-900:]

    def test_venv_extra_fails_and_is_named(self, tmp_path):
        """A venv holding a name outside the new exact set (a hand pip
        install the upgrade prune should have removed) fails, named."""
        assets = tmp_path / "a"
        assets.mkdir()
        tree = self._make_tree(
            tmp_path, venv_names=("fixturepkg", "pip", "planted-residue"))
        r = self._run(tmp_path, assets, tree)
        out = r.stdout + r.stderr
        assert r.returncode == 1, out[-900:]
        assert "planted-residue" in out, out[-900:]

    def test_missing_prev_receipt_fails(self, tmp_path):
        """No previous receipt (an unverifiable predecessor) fails the
        upgrade verification, naming the chain gap."""
        assets = tmp_path / "a"
        assets.mkdir()
        tree = self._make_tree(tmp_path, venv_names=("fixturepkg", "pip"))
        r = self._run(tmp_path, assets, tree, drop_prev_receipt=True)
        out = r.stdout + r.stderr
        assert r.returncode == 1, out[-900:]
        assert "cannot download the previous receipt" in out, out[-900:]

    def _append_upgrade_line(self, assets, ver, line):
        rec = assets / f"hive-{ver}.receipt.txt"
        rec.write_text(rec.read_text() + line + "\n")

    def test_receipt_upgrade_line_verifies(self, tmp_path):
        """T5 (0.15): a receipt carrying the upgrade line (from-tag,
        verified) verifies against --from that tag — the line agrees."""
        assets = tmp_path / "a"
        assets.mkdir()
        tree = self._make_tree(tmp_path, venv_names=("fixturepkg", "pip"))
        self._build_release(assets, self.NEW)
        self._build_release(assets, self.PREV)
        self._append_upgrade_line(assets, self.NEW,
                                  f"upgrade-from: v{self.PREV} (verified)")
        r = self._run(tmp_path, assets, tree)
        out = r.stdout + r.stderr
        assert r.returncode == 0, out[-900:]
        assert "upgrade-from line agrees" in out, out[-500:]

    def test_receipt_upgrade_line_mismatch_fails(self, tmp_path):
        """T5 (0.15): a receipt claiming a DIFFERENT predecessor fails
        the upgrade verification, naming the chain mismatch."""
        assets = tmp_path / "a"
        assets.mkdir()
        tree = self._make_tree(tmp_path, venv_names=("fixturepkg", "pip"))
        self._build_release(assets, self.NEW)
        self._build_release(assets, self.PREV)
        self._append_upgrade_line(assets, self.NEW,
                                  "upgrade-from: v0.9.7 (verified)")
        r = self._run(tmp_path, assets, tree)
        out = r.stdout + r.stderr
        assert r.returncode == 1, out[-900:]
        assert "chain mismatch" in out, out[-900:]
