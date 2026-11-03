"""Tests for hive_cli.commands — command handlers."""
import sys
import os
import json
import re
import subprocess
from pathlib import Path
# Ensure lib/ is on the path when running from within hive/ directory
_hive_lib = str(Path(__file__).resolve().parent.parent / "lib")
if _hive_lib not in sys.path:
    sys.path.insert(0, _hive_lib)



import pytest


# Ensure lib/ is on the path
os.environ.setdefault("HIVE_INSTALL_DIR", str(Path(__file__).resolve().parent.parent))


class TestCommandModulesExist:
    """Test that all command modules are importable."""

    EXPECTED_COMMANDS = [
        "agent", "backlog", "chat", "company", "config", "dashboard",
        "doctor", "gates", "gh", "init", "killall", "llm",
        "release", "restart", "round", "ticket", "tool",
    ]

    @pytest.mark.parametrize("cmd", EXPECTED_COMMANDS)
    def test_command_module_importable(self, cmd):
        """Each command module should be importable."""
        try:
            __import__(f"hive_cli.commands.{cmd}")
        except ImportError as e:
            pytest.fail(f"Cannot import hive_cli.commands.{cmd}: {e}")


class TestInitCommand:
    """Test the init command handler."""

    def test_init_handle_exists(self):
        from hive_cli.commands.init import handle
        assert callable(handle)

    def test_init_handle_signature(self):
        from hive_cli.commands.init import handle
        import inspect
        sig = inspect.signature(handle)
        params = list(sig.parameters.keys())
        assert "args" in params or len(params) >= 1


class TestTicketCommand:
    """Test the ticket command handler."""

    def test_ticket_handle_exists(self):
        from hive_cli.commands.ticket import handle
        assert callable(handle)


class TestChatCommand:
    """Test the chat command handler."""

    def test_chat_handle_exists(self):
        from hive_cli.commands.chat import handle
        assert callable(handle)


class TestAgentCommand:
    """Test the agent command handler."""

    def test_agent_handle_exists(self):
        from hive_cli.commands.agent import handle
        assert callable(handle)


class TestCompanyCommand:
    """Test the company command handler."""

    def test_company_handle_start_exists(self):
        from hive_cli.commands.company import handle_start
        assert callable(handle_start)

    def test_company_handle_stop_exists(self):
        from hive_cli.commands.company import handle_stop
        assert callable(handle_stop)

    def test_company_handle_status_exists(self):
        from hive_cli.commands.company import handle_status
        assert callable(handle_status)


class TestGatesCommand:
    """Test the gates command handler."""

    def test_gates_handle_exists(self):
        from hive_cli.commands.gates import handle
        assert callable(handle)


class TestBacklogCommand:
    """Test the backlog command handler."""

    def test_backlog_handle_exists(self):
        from hive_cli.commands.backlog import handle
        assert callable(handle)


class TestConfigCommand:
    """Test the config command handler."""

    def test_config_handle_exists(self):
        from hive_cli.commands.config import handle
        assert callable(handle)


class TestDoctorCommand:
    """Test the doctor command handler."""

    def test_doctor_handle_exists(self):
        from hive_cli.commands.doctor import handle
        assert callable(handle)


class TestKillallCommand:
    """Test the killall command handler."""

    def test_killall_handle_exists(self):
        from hive_cli.commands.killall import handle
        assert callable(handle)


class TestLLMCommand:
    """Test the llm command handler."""

    def test_llm_handle_exists(self):
        from hive_cli.commands.llm import handle
        assert callable(handle)


class TestRoundCommand:
    """Test the round command handler."""

    def test_round_handle_exists(self):
        from hive_cli.commands.round import handle
        assert callable(handle)


class TestToolCommand:
    """Test the tool command handler."""

    def test_tool_handle_invoke_exists(self):
        from hive_cli.commands.tool import handle_invoke
        assert callable(handle_invoke)

    def test_tool_handle_list_exists(self):
        from hive_cli.commands.tool import handle_list
        assert callable(handle_list)


class TestGHCommand:
    """Test the gh command handler."""

    def test_gh_handle_exists(self):
        from hive_cli.commands.gh import handle
        assert callable(handle)


class TestReleaseCommand:
    """Test the release command handler."""

    def test_release_handle_exists(self):
        from hive_cli.commands.release import handle
        assert callable(handle)


class TestDashboardCommand:
    """Test the dashboard command handler."""

    def test_dashboard_handle_exists(self):
        from hive_cli.commands.dashboard import handle
        assert callable(handle)


class TestRestartCommand:
    """Test the restart command handler."""

    def test_restart_handle_exists(self):
        from hive_cli.commands.restart import handle
        assert callable(handle)


class TestMCPClientModule:
    """Test the MCP client module."""

    def test_mcp_client_importable(self):
        from hive_cli import mcp_client
        assert hasattr(mcp_client, "call_tool")
        assert hasattr(mcp_client, "list_tools")

    def test_call_tool_exists(self):
        from hive_cli.mcp_client import call_tool
        assert callable(call_tool)

    def test_list_tools_exists(self):
        from hive_cli.mcp_client import list_tools
        assert callable(list_tools)


class TestDoctorCommandChecks:
    """Test doctor command checks."""

    def test_doctor_checks_scripts_exist(self):
        """Doctor should check for key scripts."""
        content = Path("lib/hive_cli/commands/doctor.py").read_text()
        # Should reference key scripts
        assert "ticket-create" in content or "init-workspace" in content


class TestConfigCommand:
    """Test config command."""

    def test_config_get_exists(self):
        # The handle function should process 'get' action
        pass  # Hard to test without actual config file


class TestConfigSetUpsert:
    """C2: `hive config set` does a real TOML upsert. The old line-append
    emitted a duplicate [section] header per call, which made the user
    config unparseable for every reader (doctor, harness, CLI) after the
    second set into the same section. `hive config edit` seeds a missing
    file from the tracked template instead of a blank comment."""

    install_dir = Path(__file__).resolve().parent.parent

    @staticmethod
    def _run(home, *args):
        env = {**os.environ, "HOME": str(home),
               "HIVE_ROOT": str(TestConfigSetUpsert.install_dir),
               "PYTHONPATH": str(TestConfigSetUpsert.install_dir / "lib")
               + os.pathsep + os.environ.get("PYTHONPATH", "")}
        return subprocess.run(
            [sys.executable, "-m", "hive_cli.main", "config", *args],
            capture_output=True, text=True, env=env,
            cwd=str(TestConfigSetUpsert.install_dir), timeout=60,
        )

    @staticmethod
    def _load(path):
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        return tomllib.loads(Path(path).read_text())

    def test_set_creates_file_seeded_from_template(self, tmp_path):
        r = self._run(tmp_path, "set", "llm.provider", "openai")
        assert r.returncode == 0, r.stderr
        text = (tmp_path / ".config" / "hive" / "config.toml").read_text()
        assert re.search(r'^provider\s*=\s*"openai"\s*$', text, flags=re.M), \
            "value must land on the key line (not just in a comment)"
        assert text.count("[llm]") == 1
        # Fresh file carries the template: other sections present too
        assert "[dashboard]" in text and "[release]" in text and "[tdd]" in text

    def test_set_same_section_thrice_never_duplicates_table(self, tmp_path):
        for key, val in (("llm.provider", "openai"),
                         ("llm.model", "qwen3"),
                         ("llm.api_key", "sk-test")):
            r = self._run(tmp_path, "set", key, val)
            assert r.returncode == 0, r.stderr
        path = tmp_path / ".config" / "hive" / "config.toml"
        text = path.read_text()
        assert text.count("[llm]") == 1, \
            "duplicate [llm] table — the file would be unparseable"
        data = self._load(path)
        assert data["llm"]["provider"] == "openai"
        assert data["llm"]["model"] == "qwen3"
        assert data["llm"]["api_key"] == "sk-test"

    def test_set_replaces_existing_key(self, tmp_path):
        self._run(tmp_path, "set", "llm.provider", "openai")
        r = self._run(tmp_path, "set", "llm.provider", "anthropic")
        assert r.returncode == 0, r.stderr
        text = (tmp_path / ".config" / "hive" / "config.toml").read_text()
        key_lines = re.findall(r"^provider\s*=.*$", text, flags=re.M)
        assert len(key_lines) == 1, "key must be replaced, not appended"
        assert re.search(r'^provider\s*=\s*"anthropic"\s*$', text, flags=re.M)

    def test_set_types_int_and_bool(self, tmp_path):
        self._run(tmp_path, "set", "mcp.port", "9000")
        self._run(tmp_path, "set", "logs.journal", "true")
        path = tmp_path / ".config" / "hive" / "config.toml"
        data = self._load(path)
        assert data["mcp"]["port"] == 9000
        assert data["logs"]["journal"] is True

    def test_set_new_section_appends(self, tmp_path):
        cfg = tmp_path / ".config" / "hive"
        cfg.mkdir(parents=True)
        (cfg / "config.toml").write_text("[mcp]\nport = 7891\n")
        r = self._run(tmp_path, "set", "sprint.max_active_tickets", "9")
        assert r.returncode == 0, r.stderr
        data = self._load(cfg / "config.toml")
        assert data["sprint"]["max_active_tickets"] == 9
        assert data["mcp"]["port"] == 7891

    def test_set_rejects_non_dotted_key(self, tmp_path):
        r = self._run(tmp_path, "set", "llm", "openai")
        assert r.returncode != 0
        assert "section.key" in r.stderr

    def test_config_edit_seeds_template_not_blank_comment(self, tmp_path):
        env = {**os.environ, "HOME": str(tmp_path),
               "HIVE_ROOT": str(self.install_dir), "EDITOR": "true",
               "PYTHONPATH": str(self.install_dir / "lib")
               + os.pathsep + os.environ.get("PYTHONPATH", "")}
        r = subprocess.run(
            [sys.executable, "-m", "hive_cli.main", "config", "edit"],
            capture_output=True, text=True, env=env,
            cwd=str(self.install_dir), timeout=60,
        )
        assert r.returncode == 0, r.stderr
        text = (tmp_path / ".config" / "hive" / "config.toml").read_text()
        assert "[llm]" in text and "[tdd]" in text, \
            "a missing config must be seeded from the template so the " \
            "editor shows every key with its working default"


class TestDoctorLlmHint:
    """C4: an unconfigured LLM provider must name the exact file to edit —
    the old message said 'the [llm] section of config.toml' with no path,
    which is how a stranger was pointed at a file they could not find."""

    install_dir = Path(__file__).resolve().parent.parent

    def test_doctor_names_the_user_config_file(self, tmp_path):
        env = {**os.environ, "HOME": str(tmp_path),
               "PYTHONPATH": str(self.install_dir / "lib")
               + os.pathsep + os.environ.get("PYTHONPATH", "")}
        r = subprocess.run(
            [sys.executable, "-m", "hive_cli.main", "doctor"],
            capture_output=True, text=True, env=env,
            cwd=str(self.install_dir), timeout=120,
        )
        out = r.stdout + r.stderr
        user_cfg = str(tmp_path / ".config" / "hive" / "config.toml")
        assert "LLM provider configured" in out
        assert user_cfg in out, "doctor must name the exact file to edit"
        assert "hive config edit" in out


class TestConfigIntegrity:
    """A1: a typo'd section/key in the user config must be discoverable in
    seconds, not in a failed sprint — `hive doctor` fails with the exact
    offender plus a difflib close-match suggestion, and `hive config show`
    marks unknown keys in its output. Driven against a scratch HOME."""

    install_dir = Path(__file__).resolve().parent.parent

    @staticmethod
    def _run(home, *args):
        env = {**os.environ, "HOME": str(home),
               "HIVE_ROOT": str(TestConfigIntegrity.install_dir),
               "PYTHONPATH": str(TestConfigIntegrity.install_dir / "lib")
               + os.pathsep + os.environ.get("PYTHONPATH", "")}
        return subprocess.run(
            [sys.executable, "-m", "hive_cli.main", *args],
            capture_output=True, text=True, env=env,
            cwd=str(TestConfigIntegrity.install_dir), timeout=120,
        )

    @staticmethod
    def _write_user_config(home, text):
        cfg = home / ".config" / "hive"
        cfg.mkdir(parents=True)
        (cfg / "config.toml").write_text(text)

    def test_doctor_fails_on_unknown_key_and_names_close_match(self, tmp_path):
        self._write_user_config(tmp_path, '[llms]\nprovder = "openai"\n')
        r = self._run(tmp_path, "doctor")
        assert r.returncode != 0, \
            "a typo'd config key must fail the doctor run — it would " \
            "otherwise ship invisibly into a failed sprint"
        assert "llms.provder" in r.stdout, "the exact offender must be named"
        assert "llm.provider" in r.stdout, \
            "a close-match suggestion must point at the real key"
        assert "did you mean" in r.stdout

    def test_doctor_fails_on_unknown_section(self, tmp_path):
        self._write_user_config(tmp_path, "[llms]\n")
        r = self._run(tmp_path, "doctor")
        assert r.returncode != 0
        assert "llms" in r.stdout and "did you mean" in r.stdout

    def test_doctor_fails_on_unknown_key_in_known_section(self, tmp_path):
        self._write_user_config(tmp_path, '[llm]\nprovderr = "openai"\n')
        r = self._run(tmp_path, "doctor")
        assert r.returncode != 0
        assert "llm.provderr" in r.stdout
        assert "llm.provider" in r.stdout

    def test_doctor_stays_green_on_valid_user_config(self, tmp_path):
        self._write_user_config(
            tmp_path, '[llm]\nprovider = "openai"\nmodel = "some-model"\n')
        r = self._run(tmp_path, "doctor")
        assert "unknown key" not in r.stdout, \
            "a config with only known keys must not trigger the integrity check"
        assert "unknown section" not in r.stdout

    def test_config_show_marks_unknown_keys(self, tmp_path):
        self._write_user_config(tmp_path, '[llms]\nprovder = "openai"\n')
        r = self._run(tmp_path, "config", "show")
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert "llms.provder" in data.get("_unknown_keys", []), \
            "unknown keys must be marked in the show output"

    def test_config_show_clean_has_no_unknowns(self, tmp_path):
        self._write_user_config(tmp_path, '[llm]\nprovider = "openai"\n')
        r = self._run(tmp_path, "config", "show")
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert data.get("_unknown_keys", []) == []


class TestConfigPath:
    """A3: `hive config path` answers the where-is-my-config question in
    one line — the effective user-config path (the HIVE_CONFIG override
    wins, per A2's single spelling) plus its state: created vs missing
    (with the `hive config edit` hint)."""

    install_dir = Path(__file__).resolve().parent.parent

    @staticmethod
    def _run(home, *args, extra=None):
        env = {**os.environ, "HOME": str(home),
               "HIVE_ROOT": str(TestConfigPath.install_dir),
               "PYTHONPATH": str(TestConfigPath.install_dir / "lib")
               + os.pathsep + os.environ.get("PYTHONPATH", "")}
        env.update(extra or {})
        return subprocess.run(
            [sys.executable, "-m", "hive_cli.main", "config", *args],
            capture_output=True, text=True, env=env,
            cwd=str(TestConfigPath.install_dir), timeout=60,
        )

    def test_path_matches_hive_config_override(self, tmp_path):
        override = tmp_path / "custom" / "cfg.toml"
        override.parent.mkdir()
        override.write_text('[llm]\nprovider = "openai"\n')
        r = self._run(tmp_path, "path", extra={"HIVE_CONFIG": str(override)})
        assert r.returncode == 0, r.stderr
        assert str(override) in r.stdout, \
            "the HIVE_CONFIG override must be the reported path"
        assert "created at install" in r.stdout, \
            "an existing file reports its created state"
        default = str(tmp_path / ".config" / "hive" / "config.toml")
        assert default not in r.stdout

    def test_path_missing_prints_edit_hint(self, tmp_path):
        r = self._run(tmp_path, "path")
        assert r.returncode == 0, r.stderr
        expected = str(tmp_path / ".config" / "hive" / "config.toml")
        assert expected in r.stdout
        assert "missing" in r.stdout
        assert "hive config edit" in r.stdout, \
            "the missing variant must point at the command that creates it"


class TestLlmTestCommand:
    """B1: `hive llm test` — one small round trip through the exact resolver
    path the harness uses. Prints provider, model, base_url, the key's
    SOURCE (never the key), latency, and the model's first reply line;
    exit 0/1. 'Endpoint dead' goes from a 20-agent-turn discovery to a
    2-second check. Unconfigured reuses the doctor hint that names the
    config file."""

    install_dir = Path(__file__).resolve().parent.parent

    @staticmethod
    def _run_llm_test(home, env_extra):
        env = {**os.environ, "HOME": str(home),
               "HIVE_ROOT": str(TestLlmTestCommand.install_dir),
               "PYTHONPATH": str(TestLlmTestCommand.install_dir / "lib")
               + os.pathsep + os.environ.get("PYTHONPATH", "")}
        # Clear any inherited LLM env so the scratch config decides.
        for var in ("HIVE_PROVIDER", "HIVE_MODEL", "HIVE_API_BASE",
                    "HIVE_API_KEY", "HIVE_API_STYLE", "HIVE_VERIFICATION_MODEL"):
            env.pop(var, None)
        env.update(env_extra)
        return subprocess.run(
            [sys.executable, "-m", "hive_cli.main", "llm", "test"],
            capture_output=True, text=True, env=env,
            cwd=str(TestLlmTestCommand.install_dir), timeout=60,
        )

    def test_stub_endpoint_round_trip_prints_model(self, tmp_path):
        """A local http.server returning OpenAI-style chat JSON → exit 0
        with the model name printed (and the key never printed)."""
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                self.rfile.read(n)
                body = json.dumps({
                    "id": "chatcmpl-stub", "object": "chat.completion",
                    "model": "stub-model",
                    "choices": [{"index": 0,
                                 "message": {"role": "assistant", "content": "OK"},
                                 "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 1,
                              "total_tokens": 6},
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            r = self._run_llm_test(tmp_path, {
                "HIVE_PROVIDER": "openai", "HIVE_MODEL": "stub-model",
                "HIVE_API_BASE": f"http://127.0.0.1:{port}/v1",
                "HIVE_API_KEY": "sk-test-DO-NOT-PRINT",
            })
            assert r.returncode == 0, r.stdout + "\n" + r.stderr
            out = r.stdout
            assert "stub-model" in out, "the model name must be printed"
            assert f"http://127.0.0.1:{port}/v1" in out
            assert "openai" in out
            assert "sk-test-DO-NOT-PRINT" not in r.stdout + r.stderr, \
                "the key must never be printed — only its source label"
            assert "from env HIVE_API_KEY" in out
        finally:
            server.shutdown()

    def test_connection_refused_names_base_url(self, tmp_path):
        r = self._run_llm_test(tmp_path, {
            "HIVE_PROVIDER": "openai", "HIVE_MODEL": "any-model",
            "HIVE_API_BASE": "http://127.0.0.1:1/v1",
            "HIVE_API_KEY": "k",
        })
        assert r.returncode == 1, \
            "a dead endpoint must exit 1 — that is the whole point of B1"
        out = r.stdout + r.stderr
        assert "http://127.0.0.1:1/v1" in out, \
            "the failure message must name the base_url it tried"

    def test_unconfigured_names_config_file(self, tmp_path):
        r = self._run_llm_test(tmp_path, {})
        assert r.returncode == 1
        out = r.stdout + r.stderr
        user_cfg = str(tmp_path / ".config" / "hive" / "config.toml")
        assert user_cfg in out, \
            "the unconfigured message must name the config file to edit"


class TestStatusDaemonSection:
    """B.3: `hive status` surfaces per-daemon liveness, restart_count, and
    the supervisor's dead state (restart budget exhausted) read straight
    from processes.json — independent of the MCP server, which matters
    when the MCP daemon is exactly what died."""

    DEAD_PID = 4000000  # pid_max is 4194304 — this one is not running

    @staticmethod
    def _args(project_dir, as_json=False):
        import argparse
        return argparse.Namespace(project_dir=str(project_dir), dept=None, json=as_json)

    def _seed_daemons(self, tmp_path):
        company = tmp_path / ".company"
        company.mkdir(exist_ok=True)
        (company / "processes.json").write_text(json.dumps({
            "processes": [
                {"pid": os.getpid(), "role": "hive-mcp",
                 "department": "infrastructure", "interval": "continuous",
                 "restart_count": 0},
                {"pid": self.DEAD_PID, "role": "sprint-lifecycle",
                 "department": "infrastructure", "interval": "continuous",
                 "restart_count": 5, "dead": True,
                 "dead_reason": "restart budget exhausted (5/5)"},
            ],
        }))

    def test_status_json_includes_daemon_table(self, tmp_path, monkeypatch, capsys):
        from hive_cli.commands import company
        monkeypatch.setattr(
            company.mcp_client, "call_tool",
            lambda name, payload: {"ok": True,
                                   "data": {"it": {"health": "good", "tickets": {}}}})
        self._seed_daemons(tmp_path)
        rc = company.handle_status(self._args(tmp_path, as_json=True))
        out = json.loads(capsys.readouterr().out)
        assert rc == 0
        daemons = out["daemons"]
        by_role = {d["role"]: d for d in daemons}
        assert set(by_role) == {"hive-mcp", "sprint-lifecycle"}
        assert by_role["hive-mcp"]["alive"] is True
        assert by_role["hive-mcp"]["restart_count"] == 0
        assert by_role["sprint-lifecycle"]["alive"] is False
        assert by_role["sprint-lifecycle"]["dead"] is True
        assert by_role["sprint-lifecycle"]["restart_count"] == 5

    def test_status_text_marks_dead_daemon(self, tmp_path, monkeypatch, capsys):
        from hive_cli.commands import company
        monkeypatch.setattr(
            company.mcp_client, "call_tool",
            lambda name, payload: {"ok": True, "data": {}})
        self._seed_daemons(tmp_path)
        rc = company.handle_status(self._args(tmp_path))
        out = capsys.readouterr().out
        assert rc == 0
        assert "sprint-lifecycle" in out
        assert "DEAD — restart budget exhausted (5/5)" in out
        assert "restarts=5" in out
        assert "hive stop && hive start" in out

    def test_status_shows_daemons_when_mcp_is_down(self, tmp_path, monkeypatch, capsys):
        from hive_cli.commands import company
        monkeypatch.setattr(
            company.mcp_client, "call_tool",
            lambda name, payload: {"ok": False, "error": "ConnectionError: server down"})
        self._seed_daemons(tmp_path)
        rc = company.handle_status(self._args(tmp_path))
        captured = capsys.readouterr()
        assert rc == 1
        assert "server down" in captured.err
        # The daemon section must still be printed — it is the operator's
        # only view when the MCP daemon itself is dead.
        assert "sprint-lifecycle" in captured.out


class TestRestartDashboardMissing:
    """B.4: `hive restart` fails cleanly with a useful message when no
    dashboard is recorded in processes.json — a restart implies there was
    a running dashboard, and starting one from a never-started company
    would leave a half-started state."""

    @staticmethod
    def _args(project_dir, port=49777):
        import argparse
        return argparse.Namespace(project_dir=str(project_dir), port=port)

    def test_restart_without_processes_json_fails_cleanly(self, tmp_path, capsys):
        from hive_cli.commands import restart
        rc = restart.handle(self._args(tmp_path))
        out = capsys.readouterr()
        assert rc == 1
        assert "no dashboard" in out.err.lower()
        assert "hive start" in out.err
        # Must not create company state or launch node as a side effect.
        assert not (tmp_path / ".company").exists()

    def test_restart_with_no_dashboard_entry_fails_cleanly(self, tmp_path, capsys):
        from hive_cli.commands import restart
        company = tmp_path / ".company"
        company.mkdir()
        (company / "processes.json").write_text(json.dumps({
            "processes": [
                {"pid": os.getpid(), "role": "hive-mcp",
                 "department": "infrastructure", "interval": "continuous"},
            ],
        }))
        rc = restart.handle(self._args(tmp_path))
        out = capsys.readouterr()
        assert rc == 1
        assert "no dashboard" in out.err.lower()
        assert "hive start" in out.err
        assert "hive dashboard" in out.err


class TestDoctorDaemonHealth:
    """B.3: `hive doctor` reports supervisor-managed daemon health. A
    daemon marked dead (restart budget exhausted) fails the run; a live
    daemon passes; a project without .company is left alone."""

    DEAD_PID = 4000000

    def _run_doctor(self, tmp_path, monkeypatch, capsys):
        import argparse
        from hive_cli.commands import doctor
        monkeypatch.chdir(tmp_path)
        rc = doctor.handle(argparse.Namespace(project_dir="."))
        return rc, capsys.readouterr().out

    def test_doctor_fails_on_dead_daemon(self, tmp_path, monkeypatch, capsys):
        company = tmp_path / ".company"
        company.mkdir()
        (company / "processes.json").write_text(json.dumps({"processes": [
            {"pid": self.DEAD_PID, "role": "sprint-lifecycle", "restart_count": 5,
             "dead": True, "dead_reason": "restart budget exhausted (5/5)"},
        ]}))
        rc, out = self._run_doctor(tmp_path, monkeypatch, capsys)
        assert rc == 1, "a dead daemon must fail the doctor run"
        assert "Daemon health" in out
        assert "DEAD — restart budget exhausted (5/5)" in out
        assert "hive stop && hive start" in out

    def test_doctor_reports_live_daemon(self, tmp_path, monkeypatch, capsys):
        company = tmp_path / ".company"
        company.mkdir()
        (company / "processes.json").write_text(json.dumps({"processes": [
            {"pid": os.getpid(), "role": "hive-mcp", "restart_count": 1},
        ]}))
        rc, out = self._run_doctor(tmp_path, monkeypatch, capsys)
        assert "Daemon health" in out
        assert "daemon hive-mcp" in out
        assert "DEAD" not in out.split("Daemon health")[-1]

    def test_doctor_ignores_project_without_company(self, tmp_path, monkeypatch, capsys):
        rc, out = self._run_doctor(tmp_path, monkeypatch, capsys)
        assert "Daemon health" not in out


class TestDoctorReleasePath:
    """C.6: `hive doctor` covers the release path — VERSION file, the
    *resolved* publisher script (config override or built-in default)
    existing and executable, gh CLI for the default publisher, and
    VERSION-file-vs-installed-tree (pyproject metadata) consistency.
    Each branch is exercised against a stubbed install tree."""

    def _stub_install(self, tmp_path, version="9.9.9",
                      pyproject_version="9.9.9", publisher_exec=True,
                      write_pyproject=True):
        install = tmp_path / "install"
        (install / "lib" / "templates" / "publishers").mkdir(parents=True)
        pub = install / "lib" / "templates" / "publishers" / "github.sh"
        pub.write_text("#!/bin/bash\necho publish\n")
        pub.chmod(0o755 if publisher_exec else 0o644)
        (install / "VERSION").write_text(version + "\n")
        if write_pyproject:
            (install / "packaging").mkdir()
            (install / "packaging" / "pyproject.toml").write_text(
                f'[project]\nname = "hive-os"\nversion = "{pyproject_version}"\n'
            )
        return install

    def _run_release_checks(self, tmp_path, monkeypatch, capsys,
                            install, gh_available=True):
        from hive_cli.commands import doctor
        monkeypatch.setattr(doctor, "HIVE_INSTALL_DIR", install)
        monkeypatch.setattr(doctor, "HIVE_ROOT", install)
        monkeypatch.setenv("HIVE_CONFIG", str(tmp_path / "no-such-config.toml"))
        import shutil
        monkeypatch.setattr(shutil, "which",
                            lambda name: "/usr/bin/stub" if name == "gh" and gh_available else None)
        ok = doctor._release_checks()
        return ok, capsys.readouterr().out

    # -- resolver unit tests -------------------------------------------------

    def test_resolve_publisher_default_when_no_config(self, tmp_path):
        from hive_cli.commands import doctor
        pub, source = doctor.resolve_publisher(
            tmp_path / "nope.toml", tmp_path / "default.sh")
        assert pub == tmp_path / "default.sh"
        assert source == "built-in default"

    def test_resolve_publisher_config_override(self, tmp_path):
        from hive_cli.commands import doctor
        cfg = tmp_path / "config.toml"
        cfg.write_text('[release]\npublisher_script = "/opt/my-pub.sh"\n')
        pub, source = doctor.resolve_publisher(cfg, tmp_path / "default.sh")
        assert pub == Path("/opt/my-pub.sh")
        assert source == "config override"

    def test_resolve_publisher_empty_override_falls_back(self, tmp_path):
        """publisher_script = "" must fall back to the default — the runner's
        awk treats an empty value as unset."""
        from hive_cli.commands import doctor
        cfg = tmp_path / "config.toml"
        cfg.write_text('[release]\npublisher_script = ""\n')
        pub, source = doctor.resolve_publisher(cfg, tmp_path / "default.sh")
        assert pub == tmp_path / "default.sh"
        assert source == "built-in default"

    # -- _release_checks branches -------------------------------------------

    def test_all_green(self, tmp_path, monkeypatch, capsys):
        install = self._stub_install(tmp_path)
        ok, out = self._run_release_checks(tmp_path, monkeypatch, capsys, install)
        assert ok is True, out
        assert "VERSION file" in out and "9.9.9" in out
        assert "publisher script" in out
        assert "gh CLI (default publisher)" in out

    def test_version_file_missing(self, tmp_path, monkeypatch, capsys):
        install = self._stub_install(tmp_path)
        (install / "VERSION").unlink()
        ok, out = self._run_release_checks(tmp_path, monkeypatch, capsys, install)
        assert ok is False
        assert "missing" in out

    def test_publisher_not_executable(self, tmp_path, monkeypatch, capsys):
        install = self._stub_install(tmp_path, publisher_exec=False)
        ok, out = self._run_release_checks(tmp_path, monkeypatch, capsys, install)
        assert ok is False
        assert "missing or not executable" in out

    def test_publisher_missing(self, tmp_path, monkeypatch, capsys):
        install = self._stub_install(tmp_path)
        (install / "lib" / "templates" / "publishers" / "github.sh").unlink()
        ok, out = self._run_release_checks(tmp_path, monkeypatch, capsys, install)
        assert ok is False
        assert "missing or not executable" in out

    def test_gh_missing_fails_with_default_publisher(self, tmp_path, monkeypatch, capsys):
        install = self._stub_install(tmp_path)
        ok, out = self._run_release_checks(tmp_path, monkeypatch, capsys,
                                           install, gh_available=False)
        assert ok is False
        assert "gh CLI (default publisher)" in out

    def test_custom_publisher_skips_gh_requirement(self, tmp_path, monkeypatch, capsys):
        """A config override to a working custom publisher must be honored
        (and gh must not be demanded for it — even when gh is absent)."""
        install = self._stub_install(tmp_path)
        custom = tmp_path / "my-pub.sh"
        custom.write_text("#!/bin/bash\necho local publish\n")
        custom.chmod(0o755)
        cfg = tmp_path / "config.toml"
        cfg.write_text(f'[release]\npublisher_script = "{custom}"\n')
        from hive_cli.commands import doctor
        monkeypatch.setattr(doctor, "HIVE_INSTALL_DIR", install)
        monkeypatch.setattr(doctor, "HIVE_ROOT", install)
        monkeypatch.setenv("HIVE_CONFIG", str(cfg))
        import shutil
        monkeypatch.setattr(shutil, "which", lambda name: None)  # no gh at all
        ok = doctor._release_checks()
        out = capsys.readouterr().out
        assert ok is True, out
        assert "config override" in out
        assert str(custom) in out
        assert "not required (custom publisher in effect)" in out
        assert "gh CLI (default publisher)" not in out

    def test_custom_publisher_missing_fails(self, tmp_path, monkeypatch, capsys):
        install = self._stub_install(tmp_path)
        cfg = tmp_path / "config.toml"
        cfg.write_text('[release]\npublisher_script = "/no/such/publisher.sh"\n')
        from hive_cli.commands import doctor
        monkeypatch.setattr(doctor, "HIVE_INSTALL_DIR", install)
        monkeypatch.setattr(doctor, "HIVE_ROOT", install)
        monkeypatch.setenv("HIVE_CONFIG", str(cfg))
        ok = doctor._release_checks()
        out = capsys.readouterr().out
        assert ok is False
        assert "/no/such/publisher.sh" in out

    def test_pyproject_version_stale_fails(self, tmp_path, monkeypatch, capsys):
        """The exact drift this round ships with: VERSION bumped, the
        installed tree's pyproject metadata left behind."""
        install = self._stub_install(tmp_path, version="0.7.0",
                                     pyproject_version="0.6.0")
        ok, out = self._run_release_checks(tmp_path, monkeypatch, capsys, install)
        assert ok is False
        assert "STALE" in out
        assert "VERSION=0.7.0 pyproject=0.6.0" in out

    def test_pyproject_missing_is_tolerated(self, tmp_path, monkeypatch, capsys):
        install = self._stub_install(tmp_path, write_pyproject=False)
        ok, out = self._run_release_checks(tmp_path, monkeypatch, capsys, install)
        assert ok is True, out

    def test_pyproject_without_version_field_warns(self, tmp_path, monkeypatch, capsys):
        install = self._stub_install(tmp_path, write_pyproject=False)
        (install / "packaging").mkdir()
        (install / "packaging" / "pyproject.toml").write_text('[project]\nname = "hive-os"\n')
        ok, out = self._run_release_checks(tmp_path, monkeypatch, capsys, install)
        assert ok is True, out  # warning, not a failure
        assert "no version field" in out

    # -- integration: section wired into handle() ---------------------------

    def test_handle_prints_release_section(self, tmp_path, monkeypatch, capsys):
        """Full doctor run includes the release-path section (against the
        real install dir — same pattern as the B.3 daemon-health tests)."""
        import argparse
        from hive_cli.commands import doctor
        monkeypatch.chdir(tmp_path)
        doctor.handle(argparse.Namespace(project_dir="."))
        out = capsys.readouterr().out
        assert "Release path:" in out
        assert "publisher script" in out



