"""Tests for hive_cli.commands — command handlers."""
import sys
import os
import json
import re
import shutil
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
        "release", "restart", "ticket", "tool",
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


class TestInitPreflight:
    """F2: `hive init` prints the exact fix when the resolved LLM is
    unconfigured — at the end of init, before the first spawn fails on
    it. A hint, not a gate: the scaffold is still created and the exit
    code stays 0 (doctor is the gate)."""

    install_dir = Path(__file__).resolve().parent.parent

    @classmethod
    def _clean_env(cls, home: Path) -> dict:
        """A HOME with no HIVE_* env and no user config — the
        unconfigured state the hint must catch. The dev machine's real
        env carries HIVE_* endpoint vars, so they are stripped."""
        env = {k: v for k, v in os.environ.items() if not k.startswith("HIVE_")}
        env.update({
            "HOME": str(home),
            "HIVE_ROOT": str(cls.install_dir),
            "PYTHONPATH": str(cls.install_dir / "lib")
                           + os.pathsep + os.environ.get("PYTHONPATH", ""),
        })
        return env

    def _run_init(self, tmp_path, home, name):
        project = tmp_path / name
        project.mkdir()
        # Item 5: init guard - a real company is its own git repo
        # (the scratch tmp dir sits inside the sandbox checkout).
        subprocess.run(["git", "init", "-q"], cwd=str(project), check=True)
        return subprocess.run(
            [sys.executable, "-m", "hive_cli.main", "init", str(project),
             "--brief", "preflight test project", "--github", "skip"],
            capture_output=True, text=True, timeout=180,
            cwd=str(project), env=self._clean_env(home),
        ), project

    def test_unconfigured_llm_prints_hint_and_scaffolds(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        r, project = self._run_init(tmp_path, home, "proj-unconfigured")
        assert r.returncode == 0, r.stdout + "\n" + r.stderr
        # The scaffold is created — the hint is not a gate.
        assert (project / ".company").is_dir(), \
            "init must still scaffold when the LLM is unconfigured"
        # The hint names the exact config file and the fix.
        assert "LLM preflight" in r.stdout, \
            "an unconfigured scratch HOME must print the preflight hint"
        assert "config.toml" in r.stdout
        assert "hive config edit" in r.stdout

    def test_configured_llm_prints_no_hint(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        project = tmp_path / "proj-configured"
        project.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=str(project), check=True)
        env = self._clean_env(home)
        env["HIVE_PROVIDER"] = "openai"
        env["HIVE_API_BASE"] = "https://api.openai.com/v1"
        env["HIVE_MODEL"] = "gpt-4o-mini"
        env["HIVE_API_KEY"] = "sk-test-not-a-real-key"
        r = subprocess.run(
            [sys.executable, "-m", "hive_cli.main", "init", str(project),
             "--brief", "preflight test project", "--github", "skip"],
            capture_output=True, text=True, timeout=180,
            cwd=str(project), env=env,
        )
        assert r.returncode == 0, r.stdout + "\n" + r.stderr
        assert (project / ".company").is_dir()
        assert "LLM preflight" not in r.stdout, \
            "a configured LLM must not print the preflight hint"


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



class TestStyleBRetirement:
    """G2: one write path. No CLI handler may shell out to a
    lib/tools/*.sh script to mutate .company/ state. The only permitted
    direct script calls are process management + scaffolding (start/stop/
    health/killall/init) and the dashboard/harness probers — everything
    that touches company state goes through the MCP client (Style A)."""

    REPO = Path(__file__).resolve().parent.parent

    ALLOWED_SCRIPTS = {
        # process management (must bypass MCP — agents restart-looped daemons)
        "company-start.sh", "company-stop.sh", "company-health.sh",
        "hive-killall.sh",
        # scaffolding / probers
        "init.sh", "agent-harness.py", "server.js", "server.py",
    }

    def _script_refs(self, text):
        refs = set()
        # direct literals in subprocess calls
        for m in re.finditer(r"subprocess\.(?:run|Popen|call)\(", text):
            i, depth, j = m.end(), 1, m.end()
            while depth and j < len(text):
                if text[j] == "(":
                    depth += 1
                elif text[j] == ")":
                    depth -= 1
                j += 1
            refs |= set(re.findall(r'"([a-z0-9_.-]+\.(?:sh|py|js))"', text[i:j]))
        # Path-constructed:  ... / "lib" / "tools" / "name.sh"
        refs |= set(re.findall(
            r'(?:tools|core|dashboard|templates)" */ *"([a-z0-9_.-]+\.(?:sh|py|js))"',
            text))
        return refs

    def test_no_handler_shells_out_for_state_mutation(self):
        problems = []
        for f in sorted((self.REPO / "lib" / "hive_cli" / "commands").glob("*.py")):
            for name in sorted(self._script_refs(f.read_text())):
                if name not in self.ALLOWED_SCRIPTS:
                    problems.append(f"{f.name}: {name}")
        assert not problems, (
            "CLI handlers shell out to non-process-management scripts "
            "(state mutations must go through the MCP client, Style A): "
            + ", ".join(problems))



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

    def test_set_list_key_json_array_round_trips(self, tmp_path):
        """F1: a JSON array into a list-typed key is written as a real
        TOML array and `config get` returns it as a list — so keys like
        tdd.applies_to_agents stop being edit-only."""
        r = self._run(tmp_path, "set", "tdd.applies_to_agents",
                      '["ic-engineer"]')
        assert r.returncode == 0, r.stderr
        path = tmp_path / ".config" / "hive" / "config.toml"
        data = self._load(path)
        assert data["tdd"]["applies_to_agents"] == ["ic-engineer"]
        g = self._run(tmp_path, "get", "tdd.applies_to_agents")
        assert g.returncode == 0, g.stderr
        assert json.loads(g.stdout) == ["ic-engineer"]

    def test_set_list_key_comma_separated(self, tmp_path):
        r = self._run(tmp_path, "set", "tdd.applies_to_agents",
                      "ic-engineer, ic-infra")
        assert r.returncode == 0, r.stderr
        data = self._load(tmp_path / ".config" / "hive" / "config.toml")
        assert data["tdd"]["applies_to_agents"] == ["ic-engineer", "ic-infra"]

    def test_set_list_key_single_value_is_one_item_list(self, tmp_path):
        r = self._run(tmp_path, "set", "tdd.applies_to_agents", "ic-engineer")
        assert r.returncode == 0, r.stderr
        data = self._load(tmp_path / ".config" / "hive" / "config.toml")
        assert data["tdd"]["applies_to_agents"] == ["ic-engineer"]

    def test_set_scalar_key_with_comma_stays_string(self, tmp_path):
        """F1 is schema-driven: llm.model is typed str in the defaults,
        so a comma stays inside the string — no list coercion."""
        r = self._run(tmp_path, "set", "llm.model", "a,b")
        assert r.returncode == 0, r.stderr
        data = self._load(tmp_path / ".config" / "hive" / "config.toml")
        assert data["llm"]["model"] == "a,b"

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


class TestLlmUsageLogNaming:
    """B2: the 'tiny' naming is retired — the usage log is llm-usage.log.
    Readers accept the legacy llm-tiny.log for one release."""

    install_dir = Path(__file__).resolve().parent.parent

    @staticmethod
    def _seed_usage_log(company: Path, filename: str):
        logs = company / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        (logs / filename).write_text(
            json.dumps({"ts": ts, "caller": "ic-engineer", "mode": "agent",
                        "response": "OK"}) + "\n")

    @staticmethod
    def _run(company, *args):
        env = {**os.environ, "COMPANY_DIR": str(company),
               "HIVE_ROOT": str(TestLlmUsageLogNaming.install_dir),
               "PYTHONPATH": str(TestLlmUsageLogNaming.install_dir / "lib")
               + os.pathsep + os.environ.get("PYTHONPATH", "")}
        return subprocess.run(
            [sys.executable, "-m", "hive_cli.main", "llm", *args],
            capture_output=True, text=True, env=env,
            cwd=str(TestLlmUsageLogNaming.install_dir), timeout=60,
        )

    def test_stats_reads_new_usage_log(self, tmp_path):
        company = tmp_path / ".company"
        company.mkdir()
        self._seed_usage_log(company, "llm-usage.log")
        r = self._run(company, "stats")
        assert r.returncode == 0, r.stderr
        assert "llm-usage.log" in r.stdout
        assert "ic-engineer" in r.stdout

    def test_tail_reads_new_usage_log(self, tmp_path):
        company = tmp_path / ".company"
        company.mkdir()
        self._seed_usage_log(company, "llm-usage.log")
        r = self._run(company, "tail")
        assert r.returncode == 0, r.stderr
        assert "ic-engineer" in r.stdout

    def test_stats_still_reads_legacy_tiny_log(self, tmp_path):
        """A file with only the legacy name is still read — one release of
        backward compatibility for existing .company workspaces."""
        company = tmp_path / ".company"
        company.mkdir()
        self._seed_usage_log(company, "llm-tiny.log")
        r = self._run(company, "stats")
        assert r.returncode == 0, r.stderr
        assert "llm-tiny.log" in r.stdout, \
            "the legacy log must still be found and named"
        assert "ic-engineer" in r.stdout

    def test_stats_missing_log_names_the_new_log(self, tmp_path):
        company = tmp_path / ".company"
        company.mkdir()
        r = self._run(company, "stats")
        assert r.returncode == 1
        assert "llm-usage.log" in r.stdout + r.stderr


class TestLlmStatsWindow:
    """B2 (0.9.0): `hive llm stats` — windowed token/cost totals.

    Replaces the fixed 24h/1h pair with --window 1h|24h|7d|all, adds
    token + cost totals aggregated by model, and --json for scraping.
    Entries without a cost field (pre-0.9.0) sum as 0, not an error.
    """

    install_dir = Path(__file__).resolve().parent.parent

    @staticmethod
    def _seed(company: Path, entries):
        logs = company / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        (logs / "llm-usage.log").write_text(
            "".join(json.dumps(e) + "\n" for e in entries))

    @staticmethod
    def _run(company, *args):
        env = {**os.environ, "COMPANY_DIR": str(company),
               "HIVE_ROOT": str(TestLlmStatsWindow.install_dir),
               "PYTHONPATH": str(TestLlmStatsWindow.install_dir / "lib")
               + os.pathsep + os.environ.get("PYTHONPATH", "")}
        return subprocess.run(
            [sys.executable, "-m", "hive_cli.main", "llm", *args],
            capture_output=True, text=True, env=env,
            cwd=str(TestLlmStatsWindow.install_dir), timeout=60,
        )

    @staticmethod
    def _entries(now):
        from datetime import timedelta
        def mk(hours_ago, tokens, cost, model="gpt-4o", caller="ic-engineer"):
            ts = (now - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
            return {"ts": ts, "caller": caller, "mode": "agent", "model": model,
                    "turn": 1, "tokens": tokens, "cost_usd": cost, "response": "x"}
        return [
            mk(0.5, 100, 0.001),      # inside 1h
            mk(12, 200, 0.002),       # inside 24h
            mk(3 * 24, 300, 0.003),   # inside 7d
            mk(10 * 24, 400, 0.004),  # only in all
            mk(1.5, 50, 0.0),         # legacy shape: no cost field, 1h
        ]

    EXPECTED = {
        "1h": (1, 100, 0.001),    # only the 0.5h entry
        "24h": (3, 350, 0.003),   # + 12h entry + 1.5h legacy entry
        "7d": (4, 650, 0.006),    # + 3d entry
        "all": (5, 1050, 0.010),  # + 10d entry
    }

    def test_each_window_returns_correct_totals_json(self, tmp_path):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        company = tmp_path / ".company"
        company.mkdir()
        self._seed(company, self._entries(now))
        for window, (calls, tokens, cost) in self.EXPECTED.items():
            r = self._run(company, "stats", "--window", window, "--json")
            assert r.returncode == 0, (window, r.stderr)
            j = json.loads(r.stdout)
            assert j["window"] == window
            assert j["calls"] == calls, (window, j)
            assert j["tokens"] == tokens, (window, j)
            assert j["cost_usd"] == pytest.approx(cost), (window, j)
            m = j["by_model"]["gpt-4o"]
            assert (m["calls"], m["tokens"], m["cost_usd"]) == \
                (calls, tokens, pytest.approx(cost)), (window, m)

    def test_json_matches_text_run(self, tmp_path):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        company = tmp_path / ".company"
        company.mkdir()
        self._seed(company, self._entries(now))
        rj = self._run(company, "stats", "--window", "24h", "--json")
        j = json.loads(rj.stdout)
        r = self._run(company, "stats", "--window", "24h")
        assert r.returncode == 0, r.stderr
        assert f"calls:  {j['calls']}" in r.stdout
        assert f"tokens: {j['tokens']}" in r.stdout
        assert f"cost:   ${j['cost_usd']:.6f}" in r.stdout
        assert "By model" in r.stdout and "gpt-4o" in r.stdout

    def test_legacy_entry_without_cost_sums_zero(self, tmp_path):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        company = tmp_path / ".company"
        company.mkdir()
        e = self._entries(now)[4]  # the cost-less entry (1.5h old)
        self._seed(company, [e])
        r = self._run(company, "stats", "--window", "24h", "--json")
        assert r.returncode == 0, r.stderr
        j = json.loads(r.stdout)
        assert j["calls"] == 1 and j["tokens"] == 50
        assert j["cost_usd"] == pytest.approx(0.0)

    def test_non_numeric_cost_field_sums_zero_not_crash(self, tmp_path):
        """A cost_usd that is present but not numeric counts as 0 — the
        same 'never crash' contract the numeric tokens field already
        honors (its try/except). The comment above both says so; only
        cost was missing the guard, so one bad entry took down the
        whole `hive llm stats` run with a traceback."""
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        company = tmp_path / ".company"
        company.mkdir()
        e = dict(self._entries(now)[0])  # a normal entry, inside 1h
        e["cost_usd"] = "n/a"
        self._seed(company, [e])
        r = self._run(company, "stats", "--window", "1h", "--json")
        assert r.returncode == 0, (r.stdout[-400:], r.stderr[-400:])
        j = json.loads(r.stdout)
        assert j["calls"] == 1
        assert j["tokens"] == 100
        assert j["cost_usd"] == pytest.approx(0.0)


class TestDoctorJson:
    """C1: `hive doctor --json` — a machine-readable dump of every check
    (name, ok, detail) on stdout; the exit code is unchanged from the
    pretty run. The dump carries the resolved LLM identity (provider,
    model, base_url, key SOURCE — never the key) for the dashboard."""

    @staticmethod
    def _args(json_mode):
        import argparse
        return argparse.Namespace(project_dir=".", json=json_mode, deep=False)

    def test_doctor_json_parses_and_exit_matches_pretty(self, tmp_path, monkeypatch, capsys):
        from hive_cli.commands import doctor
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("HIVE_CONFIG", raising=False)
        for var in ("HIVE_PROVIDER", "HIVE_MODEL", "HIVE_API_BASE", "HIVE_API_KEY"):
            monkeypatch.delenv(var, raising=False)

        capsys.readouterr()
        rc_pretty = doctor.handle(self._args(False))
        capsys.readouterr()
        rc_json = doctor.handle(self._args(True))

        out = capsys.readouterr().out
        data = json.loads(out)  # the entire stdout is JSON
        assert set(data.keys()) >= {"ok", "checks", "llm"}
        assert rc_json == rc_pretty,             "the --json run must exit like the pretty run"
        assert data["ok"] is (rc_json == 0)

    def test_doctor_json_every_check_has_name_ok_detail(self, tmp_path, monkeypatch, capsys):
        from hive_cli.commands import doctor
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))
        doctor.handle(self._args(True))
        data = json.loads(capsys.readouterr().out)
        assert len(data["checks"]) > 10
        for c in data["checks"]:
            assert set(c.keys()) == {"name", "ok", "detail"}
            assert isinstance(c["ok"], bool)

    def test_doctor_json_llm_check_carries_resolved_identity(self, tmp_path, monkeypatch, capsys):
        """The LLM check must carry the resolved provider/base_url (and the
        key's source label — never the key value)."""
        from hive_cli.commands import doctor
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("HIVE_PROVIDER", "openai")
        monkeypatch.setenv("HIVE_MODEL", "json-model")
        monkeypatch.setenv("HIVE_API_BASE", "http://127.0.0.1:1/v1")
        monkeypatch.setenv("HIVE_API_KEY", "sk-should-never-appear")
        doctor.handle(self._args(True))
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["llm"]["configured"] is True
        assert data["llm"]["provider"] == "openai"
        assert data["llm"]["model"] == "json-model"
        assert data["llm"]["base_url"] == "http://127.0.0.1:1/v1"
        assert data["llm"]["key_source"] == "env HIVE_API_KEY"
        assert "sk-should-never-appear" not in out,             "the key value must never appear in the dump"
        llm_checks = [c for c in data["checks"] if c["name"] == "LLM provider configured"]
        assert llm_checks and llm_checks[0]["ok"] is True
        assert "json-model" in llm_checks[0]["detail"]

    def test_doctor_json_unconfigured_llm(self, tmp_path, monkeypatch, capsys):
        from hive_cli.commands import doctor
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))
        for var in ("HIVE_PROVIDER", "HIVE_MODEL", "HIVE_API_BASE", "HIVE_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        doctor.handle(self._args(True))
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["llm"]["configured"] is False
        user_cfg = str(tmp_path / ".config" / "hive" / "config.toml")
        assert user_cfg in out,             "the unconfigured dump must name the config file (C4/B1 message)"


class TestDoctorEnvFile:
    """D1: `hive doctor` warns when the daemon env file still carries
    retired variable names — the pre-unification ANTHROPIC_*/LLM_TINY_*
    spellings and the HIVE_* LLM vars that duplicate [llm]. Warning only
    (env.sh may still hold PATH/GITHUB_TOKEN); the migration line points
    at [llm] in config.toml."""

    install_dir = Path(__file__).resolve().parent.parent

    @staticmethod
    def _run(tmp_path):
        env = {**os.environ, "HOME": str(tmp_path),
               "HIVE_ROOT": str(TestDoctorEnvFile.install_dir),
               "PYTHONPATH": str(TestDoctorEnvFile.install_dir / "lib")
               + os.pathsep + os.environ.get("PYTHONPATH", "")}
        env.pop("HIVE_ENV_FILE", None)
        return subprocess.run(
            [sys.executable, "-m", "hive_cli.main", "doctor"],
            capture_output=True, text=True, env=env,
            cwd=str(TestDoctorEnvFile.install_dir), timeout=120,
        )

    def test_doctor_warns_and_names_retired_vars(self, tmp_path):
        envsh = tmp_path / ".config" / "hive" / "env.sh"
        envsh.parent.mkdir(parents=True)
        envsh.write_text(
            "export PATH=$PATH:/opt/bin\n"
            "export ANTHROPIC_API_KEY=sk-retired\n"
            "LLM_TINY_URL=http://old-endpoint\n"
            "HIVE_MODEL=old-model\n")
        r = self._run(tmp_path)
        out = r.stdout
        assert "ANTHROPIC_API_KEY" in out,             "each retired var must be named, not just counted"
        assert "LLM_TINY_URL" in out
        assert "HIVE_MODEL" in out
        assert "config.toml" in out,             "the migration line must point at [llm] in config.toml"
        assert "PATH" in out and "GITHUB_TOKEN" in out,             "the warning must state what env.sh is for"

    def test_clean_env_sh_produces_no_warning(self, tmp_path):
        envsh = tmp_path / ".config" / "hive" / "env.sh"
        envsh.parent.mkdir(parents=True)
        envsh.write_text("export PATH=$PATH:/opt/bin\nGITHUB_TOKEN=ghx\n")
        r = self._run(tmp_path)
        assert "WARN" not in r.stdout, r.stdout

    def test_missing_env_sh_produces_no_warning(self, tmp_path):
        r = self._run(tmp_path)
        assert "WARN" not in r.stdout, r.stdout


class TestStatusConfigDrift:
    """D2: `hive status` surfaces config drift — when the user config was
    modified after the daemons started, the running daemons still hold
    the OLD config (env was baked at spawn time). One line says so."""

    install_dir = Path(__file__).resolve().parent.parent

    def _seed(self, tmp_path, started_at, cfg_mtime):
        company = tmp_path / ".company"
        company.mkdir(exist_ok=True)
        (company / "processes.json").write_text(json.dumps({
            "started_at": started_at,
            "processes": [
                {"pid": os.getpid(), "role": "hive-mcp",
                 "started_at": started_at}]}))
        cfg = tmp_path / ".config" / "hive" / "config.toml"
        cfg.parent.mkdir(parents=True)
        cfg.write_text("[llm]\nprovider = \"openai\"\n")
        time_ns = int(cfg_mtime * 1e9)
        os.utime(cfg, ns=(time_ns, time_ns))
        return company

    def _run_status(self, tmp_path, monkeypatch, capsys, as_json=False):
        import argparse
        from hive_cli.commands import company
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("HIVE_CONFIG", raising=False)
        monkeypatch.setattr(
            company.mcp_client, "call_tool",
            lambda name, payload: {"ok": True,
                                   "data": {"it": {"health": "good",
                                                    "tickets": {}}}})
        args = argparse.Namespace(project_dir=str(tmp_path), dept=None,
                                  json=as_json)
        rc = company.handle_status(args)
        return rc, capsys.readouterr().out

    def test_drift_shown_when_config_newer_than_daemons(self, tmp_path,
                                                        monkeypatch, capsys):
        import time
        now = time.time()
        self._seed(tmp_path, "2026-11-01T00:00:00Z", now)  # config just touched
        rc, out = self._run_status(tmp_path, monkeypatch, capsys)
        assert "config.toml changed after daemons started" in out
        assert "hive stop && hive start" in out

    def test_no_drift_when_config_older_than_daemons(self, tmp_path,
                                                     monkeypatch, capsys):
        import time
        now = time.time()
        self._seed(tmp_path, "2026-11-02T00:00:00Z", now - 86400 * 30)
        rc, out = self._run_status(tmp_path, monkeypatch, capsys)
        assert "changed after daemons started" not in out

    def test_json_mode_carries_drift_note(self, tmp_path, monkeypatch, capsys):
        import time
        now = time.time()
        self._seed(tmp_path, "2026-11-01T00:00:00Z", now)
        rc, out = self._run_status(tmp_path, monkeypatch, capsys, as_json=True)
        data = json.loads(out)
        assert "changed after daemons started" in data.get("config_drift", "")


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


class TestCliCompanyDirEnv:
    """CLI commands must honor the COMPANY_DIR environment variable, not
    just CWD — the codebase-wide convention (every bash tool resolves
    COMPANY_DIR="${COMPANY_DIR:-$PWD/.company}", `hive llm stats` does
    the same, and the MCP tools fall back to it when the CLI passes no
    company_dir). A command run from a directory that has its own
    .company (such as a repo checkout carrying a live company) must not
    silently retarget round/release/ticket/chat/backlog/gates to the
    wrong company when COMPANY_DIR points elsewhere."""

    @staticmethod
    def _fixture_company() -> Path:
        return Path(__file__).resolve().parent / "fixtures" / "company"

    @staticmethod
    def _cli(cwd: Path, company_dir: str, *argv: str) -> dict:
        r = subprocess.run(
            [sys.executable, "-m", "hive_cli.main", *argv],
            capture_output=True, text=True, timeout=120,
            cwd=str(cwd),
            env={**os.environ,
                 "PYTHONPATH": str(Path(__file__).resolve()
                                   .parent.parent / "lib"),
                 # dead port — force the stdio MCP fallback so a live
                 # HTTP server on the default port can never answer
                 "HIVE_MCP_PORT": "1",
                 "COMPANY_DIR": company_dir})
        assert r.returncode == 0, f"{argv} rc={r.returncode}\n{r.stdout}\n{r.stderr}"
        return json.loads(r.stdout[r.stdout.find("{"):])

    def test_release_status_honors_company_dir_env(self, tmp_path):
        (tmp_path / ".company").mkdir(parents=True)
        # release_status returns the sprint state dict directly (no
        # shell-run data wrapper).
        out = self._cli(tmp_path, str(self._fixture_company()),
                        "release", "status")
        assert out.get("sprint_id") == "sprint-20261103T000000Z", (
            "release status read the CWD company, not $COMPANY_DIR: "
            f"{out}")



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


class TestDoctorProcessesJsonCorrupt:
    """E3 (0.9.0): doctor detects processes.json corruption."""

    @staticmethod
    def _run(tmp_path):
        lib = Path(__file__).resolve().parent.parent / "lib"
        return subprocess.run(
            [sys.executable, "-m", "hive_cli.main", "doctor", str(tmp_path)],
            capture_output=True, text=True, timeout=180,
            env={**os.environ, "PYTHONPATH": str(lib)})

    def test_corrupt_processes_json_fails_with_hint(self, tmp_path):
        comp = tmp_path / ".company"
        comp.mkdir()
        (comp / "processes.json").write_text('{"processes": [oops')
        r = self._run(tmp_path)
        out = r.stdout + r.stderr
        assert "CORRUPT" in out, out[-800:]
        assert "processes.json" in out
        assert "hive stop && hive start" in out

    def test_valid_processes_json_not_flagged(self, tmp_path):
        comp = tmp_path / ".company"
        comp.mkdir()
        (comp / "processes.json").write_text('{"processes": []}')
        r = self._run(tmp_path)
        assert "CORRUPT" not in r.stdout + r.stderr


#: T2 (0.13): the doctor-probe scratch installs share a session-scoped
#: venv base (built once per worker) instead of a per-test venv build.
_PROBE_VENV_BASES: dict = {}


def _build_probe_root(root: Path, with_system_site: bool) -> bool:
    """The ONE venv build site for the probe family (T2, 0.13).

    A scratch install root: the real harness sources plus a REAL venv
    (the 0.8.3 scenario, but in the venv where agent-spawn.sh actually
    runs the harness, not on the CLI's own interpreter).

    with_system_site=False: a plain venv — fresh site-packages, so the
    venv python lacks httpx (a dead spawn path) even when the CLI's
    interpreter has it.
    with_system_site=True: a --system-site-packages venv — the venv
    python inherits the base env's httpx (a healthy spawn path when the
    base env has it). Returns False if the venv cannot be created."""
    repo = Path(__file__).resolve().parent.parent
    core = root / "lib" / "core"
    core.mkdir(parents=True, exist_ok=True)
    for name in ("agent-harness.py", "llm_providers.py", "mcp_http.py"):
        shutil.copy(repo / "lib" / "core" / name, core / name)
    args = [sys.executable, "-m", "venv", str(root / "venv")]
    if with_system_site:
        args.append("--system-site-packages")
    r = subprocess.run(args, capture_output=True, text=True, timeout=180)
    return r.returncode == 0


@pytest.fixture(scope="session", autouse=True)
def _probe_venv_bases(tmp_path_factory):
    """T2 (0.13): the probe family's scratch installs, built ONCE per
    session (per xdist worker): a plain venv (no httpx — the dead spawn
    path) and a --system-site-packages venv (the healthy path). The
    doctor probe is READ-ONLY against the install root (the only runtime
    writes are throwaway .pyc files in fresh __pycache__ dirs — new
    inodes, never an in-place write through a shared one), so the
    probes share the base directly. A test that ever needs a WRITABLE
    install root must copy the base locally first (shutil.copytree).
    A base that fails to build is absent from _PROBE_VENV_BASES and the
    dependent test skips (the pre-T2 per-test skip semantics)."""
    base_dir = tmp_path_factory.mktemp("probe-venv-base")
    for key in (False, True):
        if _build_probe_root(base_dir / ("plain" if not key else "sys"),
                             with_system_site=key):
            _PROBE_VENV_BASES[key] = base_dir / ("plain" if not key else "sys")
    return _PROBE_VENV_BASES


class TestDoctorHarnessProbe:
    """A3 (0.9.0): doctor runs the A2 spawn path probe.

    T2 (0.13): the probe's scratch installs share the session-scoped
    venv base (_probe_venv_bases) — a per-test `python -m venv` build
    (~2.6s each here, more on CI runners) dominated this family; the
    probe is read-only against the root, so no copy is needed."""

    @staticmethod
    def _cli_has_httpx() -> bool:
        r = subprocess.run([sys.executable, "-c", "import httpx"],
                           capture_output=True)
        return r.returncode == 0

    @staticmethod
    def _probe_root(with_system_site: bool) -> Path:
        """The shared session base install root for this probe flavour.
        Skips (pre-T2 semantics) if the session build failed."""
        root = _PROBE_VENV_BASES.get(with_system_site)
        if root is None:
            pytest.skip("cannot create a scratch venv for the probe test "
                        "(session base build failed)")
        return root

    @staticmethod
    def _run_at(tmp_path, root: Path):
        return subprocess.run(
            [sys.executable, "-m", "hive_cli.main", "doctor", str(tmp_path)],
            capture_output=True, text=True, timeout=180,
            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve()
                                               .parent.parent / "lib"),
                 "HIVE_ROOT": str(root),
                 "HIVE_INSTALL_DIR": str(root),
                 "HIVE_PROVIDER": "openai", "HIVE_MODEL": "probe",
                 "HIVE_API_BASE": "http://127.0.0.1:9", "HIVE_API_KEY": "k"})

    def test_doctor_runs_spawn_probe(self, tmp_path, monkeypatch):
        for k, v in {"HIVE_PROVIDER": "openai", "HIVE_MODEL": "probe",
                     "HIVE_API_BASE": "http://127.0.0.1:9",
                     "HIVE_API_KEY": "k"}.items():
            monkeypatch.setenv(k, v)
        r = TestDoctorProcessesJsonCorrupt._run(tmp_path)
        out = r.stdout + r.stderr
        assert "[selftest] ok interpreter" in out, out[-600:]
        assert "[selftest] ok llm_providers.resolve" in out
        assert "agent harness (spawn path probe)" in out

    def test_doctor_probes_under_venv_python_not_cli(self, tmp_path):
        """The probe must run under the interpreter agent-spawn.sh picks
        (the install venv's python), not the CLI's: a venv whose python
        lacks httpx is a dead spawn path, and doctor has to fail on the
        import step — the same diagnosis `hive selftest` gives — instead
        of running the probe under a healthy CLI interpreter."""
        if not self._cli_has_httpx():
            pytest.skip("CLI python lacks httpx — the broken-venv probe "
                        "case is indistinguishable from a broken CLI")
        root = self._probe_root(with_system_site=False)
        r = self._run_at(tmp_path, root)
        out = r.stdout + r.stderr
        assert "FAIL import httpx" in out, (
            "doctor did not fail on the venv's missing httpx: " + out[-800:])
        assert "FAIL interpreter" not in out, (
            "doctor's probe ran under the CLI interpreter, not the "
            "spawn interpreter: " + out[-800:])
        assert "SELFTEST: OK" not in out

    def test_doctor_green_on_healthy_venv(self, tmp_path):
        """Healthy install root (venv python has httpx): the probe runs
        under the venv python and reports all steps ok."""
        if not self._cli_has_httpx():
            pytest.skip("CLI python lacks httpx — the 'healthy' --system-"
                        "site-packages venv would lack it too")
        root = self._probe_root(with_system_site=True)
        r = self._run_at(tmp_path, root)
        out = r.stdout + r.stderr
        assert "SELFTEST: OK" in out, out[-800:]
        assert "[selftest] ok interpreter" in out, out[-800:]
        assert "FAIL" not in out.replace("spawn path probe failed", "")

class TestLlmTestEmptyReply:
    """Item 12: an empty reply with ok: true is a yellow light, not a
    green one - the persisted llm-test.json must carry a warning."""

    def test_empty_reply_records_warning(self, tmp_path):
        import json
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
        from hive_cli.commands import llm

        company = tmp_path / ".company"
        company.mkdir()
        old = dict(os.environ)
        os.environ["COMPANY_DIR"] = str(company)
        try:
            llm._TEST_RECORD.clear()
            llm._TEST_RECORD.update(provider="openai", model="m", base_url="u",
                                    key_source="none", latency_ms=1,
                                    reply="", error="")
            rc = llm._save_llm_test(0)
            assert rc == 0  # best-effort: never changes rc
            rec = json.loads((company / "state" / "llm-test.json").read_text())
            assert rec["ok"] is True
            assert rec.get("warning"), (
                f"empty reply persisted without a warning: {rec}"
            )
        finally:
            os.environ.clear()
            os.environ.update(old)

    def test_nonempty_reply_has_no_warning(self, tmp_path):
        import json
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
        from hive_cli.commands import llm

        company = tmp_path / ".company"
        company.mkdir()
        old = dict(os.environ)
        os.environ["COMPANY_DIR"] = str(company)
        try:
            llm._TEST_RECORD.clear()
            llm._TEST_RECORD.update(provider="openai", model="m", base_url="u",
                                    key_source="none", latency_ms=1,
                                    reply="pong", error="")
            llm._save_llm_test(0)
            rec = json.loads((company / "state" / "llm-test.json").read_text())
            assert rec["ok"] is True
            assert "warning" not in rec
        finally:
            os.environ.clear()
            os.environ.update(old)


class TestProbeVenvBaseContract:
    """T2 (0.13) — the probe family's venv is session-scoped by
    construction, not per-test.

    A per-test `python -m venv` build (~2.6s each here, more on CI
    runners) dominated this family's wall time. The contract: EXACTLY
    ONE venv build site, module-level (the _build_probe_root helper the
    session fixture calls twice — plain + system-site), and NONE inside
    the probe class (a class-level build is reachable per test). Red
    against the pre-T2 module, where the build sat in the class's
    _scratch_install and every test paid for a fresh venv.
    """

    #: Assembled from parts so this guard's own source does not carry
    #: the contiguous literal it counts (the repo's credential-pattern
    #: guards use the same trick).
    _MARKER = '"-m"' + ", " + '"venv"'

    def test_single_module_level_venv_build_site(self):
        import inspect
        mod_src = inspect.getsource(sys.modules[__name__])
        n = mod_src.count(self._MARKER)
        assert n == 1, (
            f"the probe family must have exactly one venv build site "
            f"(the session base helper); found {n} — a per-test venv "
            "build has crept back in")
        cls_src = inspect.getsource(TestDoctorHarnessProbe)
        assert self._MARKER not in cls_src, (
            "a venv build inside the probe class is a per-test build — "
            "it must live in the module-level session base helper")

    def test_base_fixture_is_session_scoped(self):
        fx = _probe_venv_bases
        # pytest 8 spells the marker _pytestfixturefunction, pytest 9
        # _fixture_function_marker — accept both.
        meta = (getattr(fx, "_fixture_function_marker", None)
                or getattr(fx, "_pytestfixturefunction", None))
        assert meta is not None, "_probe_venv_bases must be a fixture"
        assert getattr(meta, "scope", None) == "session", (
            "the venv base must be session-scoped (built once per "
            f"worker); it is '{getattr(meta, 'scope', None)}'-scoped")


class TestDoctorInstalledSet:
    """T3 (0.13): doctor verifies the installed set at DOCTOR time.

    The installed MANIFEST.txt (the tarball's own bill of materials —
    shipped to the install via entries.list) plus the shared
    required-set constant (packaging/required-entries.list: ONE list,
    four consumers — cut gate 4, install.sh post-stage, this check,
    and the pinning tests) make the 0.12 trust surface verifiable
    after install, not only at install time. The check verifies:
      (1) the manifest exists — a dev install (git checkout, where the
          build generates it) has none: WARN, not FAIL
      (2) every REQUIRED entry exists at the install root
      (3) state-free: the manifest carries no STATE entry AND the
          install root holds no runtime-state directory (STATE minus
          venv — install.sh legitimately creates venv/)
    Names each missing or foreign entry. Red against the pre-T3
    doctor, which had no installed-set check at all (the missing
    entry and the state dir went unreported).
    """

    #: The REQUIRED section of packaging/required-entries.list — kept
    #: in lockstep by TestRequiredEntriesConstant (test_installation.py).
    REQUIRED = (
        "bin/hive",
        "lib/core/agent-spawn.sh",
        "lib/core/agent-harness.py",
        "lib/hive_mcp/server.py",
        "lib/dashboard/server.js",
        "etc/config.defaults.toml",
        "etc/config.template.toml",
        "packaging/requirements.txt",
        # T1 (0.14): the venv lock (doctor's venv-lock check reads it
        # from the install; _install creates it, TestDoctorVenvLock
        # rewrites its content).
        "packaging/requirements.lock.txt",
    )

    @classmethod
    def _install(cls, root: Path, manifest_entries) -> Path:
        """A scratch install root carrying the check's inputs: the
        required entries (empty files), the shared constant, and a
        MANIFEST.txt with the given entries."""
        repo = Path(__file__).resolve().parent.parent
        for e in cls.REQUIRED:
            p = root / e
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x")
        pk = root / "packaging"
        pk.mkdir(exist_ok=True)
        shutil.copy(repo / "packaging" / "required-entries.list",
                    pk / "required-entries.list")
        # doctor's other checks import/parse real files from the
        # install: llm_providers (stdlib-only) from HIVE_ROOT/lib/core,
        # and the config TOMLs (doctor parses them — empty stubs would
        # crash the run, not the check under test).
        core = root / "lib" / "core"
        core.mkdir(parents=True, exist_ok=True)
        shutil.copy(repo / "lib" / "core" / "llm_providers.py",
                    core / "llm_providers.py")
        for f in ("config.defaults.toml", "config.template.toml"):
            shutil.copy(repo / "etc" / f, root / "etc" / f)
        (root / "MANIFEST.txt").write_text(
            "\n".join(manifest_entries) + "\n")
        return root

    @classmethod
    def _installed_check(cls, tmp_path, root: Path) -> dict:
        """Run doctor --json against the scratch install; return the
        installed-set check dict (raises if the check is absent)."""
        r = subprocess.run(
            [sys.executable, "-m", "hive_cli.main", "doctor", "--json",
             str(tmp_path / "proj")],
            capture_output=True, text=True, timeout=180,
            env={**os.environ,
                 "PYTHONPATH": str(Path(__file__).resolve().parent.parent
                                   / "lib"),
                 "HIVE_INSTALL_DIR": str(root), "HIVE_ROOT": str(root)})
        payload = json.loads(r.stdout)
        for c in payload["checks"]:
            if c["name"].startswith("installed set"):
                return c
        raise AssertionError(
            "no installed-set check in doctor output: "
            f"{[c['name'] for c in payload['checks']]}")

    def test_doctor_green_on_clean_install(self, tmp_path):
        """(a) a clean installed set (manifest present, required
        complete, state-free) passes the check."""
        entries = list(self.REQUIRED) + ["MANIFEST.txt"]
        root = self._install(tmp_path / "install", entries)
        c = self._installed_check(tmp_path, root)
        assert c["ok"], f"clean install must pass: {c}"
        assert "state-free" in c["detail"], c

    def test_doctor_red_naming_removed_required_entry(self, tmp_path):
        """(b) a required entry removed from the install (listed in
        the manifest, gone from disk) fails the check NAMING IT."""
        entries = list(self.REQUIRED) + ["MANIFEST.txt"]
        root = self._install(tmp_path / "install", entries)
        (root / "lib" / "hive_mcp" / "server.py").unlink()
        c = self._installed_check(tmp_path, root)
        assert not c["ok"], f"must fail with a missing required entry: {c}"
        assert "lib/hive_mcp/server.py" in c["detail"], c

    def test_doctor_red_naming_state_dir_in_install(self, tmp_path):
        """(c) a runtime-state directory present at the install root
        (.company/ — state leaked into the install) fails the check
        naming it."""
        entries = list(self.REQUIRED) + ["MANIFEST.txt"]
        root = self._install(tmp_path / "install", entries)
        (root / ".company" / "tickets").mkdir(parents=True)
        c = self._installed_check(tmp_path, root)
        assert not c["ok"], f"must fail with .company/ in the install: {c}"
        assert ".company" in c["detail"], c



    def test_doctor_red_naming_state_entry_in_manifest(self, tmp_path):
        """(d) a manifest carrying a runtime-state entry (gate 4's
        state-free rule) fails the check naming the entry."""
        entries = list(self.REQUIRED) + ["MANIFEST.txt",
                                         ".company/tickets/state.json"]
        root = self._install(tmp_path / "install", entries)
        c = self._installed_check(tmp_path, root)
        assert not c["ok"], f"must fail on a state manifest entry: {c}"
        assert ".company/tickets/state.json" in c["detail"], c

    def test_dev_install_without_manifest_skips_not_fails(self, tmp_path):
        """(e) a dev install (from a git checkout — the build generates
        the manifest, so it is absent) gets a WARN, not a FAIL."""
        root = self._install(tmp_path / "install",
                             list(self.REQUIRED) + ["MANIFEST.txt"])
        (root / "MANIFEST.txt").unlink()
        c = self._installed_check(tmp_path, root)
        assert c["ok"], f"dev install (no manifest) must not fail: {c}"

    def test_install_marker_is_neutral_to_the_check(self, tmp_path):
        """(f) T3 (0.14): the .hive-install marker (written by
        install.sh --copy-to at the install root) must not affect the
        installed-set check — it is a root FILE outside the entry set
        and the manifest, and the state-free rule checks STATE dirs,
        never the marker."""
        entries = list(self.REQUIRED) + ["MANIFEST.txt"]
        root = self._install(tmp_path / "install", entries)
        (root / ".hive-install").write_text(
            "version=0.13.0\ninstalled_at=2026-11-09T00:00:00Z\n"
            "mode=copy-to\n")
        c = self._installed_check(tmp_path, root)
        assert c["ok"], f"the install marker must not fail the check: {c}"
        assert ".hive-install" not in c["detail"], c


@pytest.fixture(scope="session")
def _venv_lock_base(tmp_path_factory):
    """T1 (0.14): the venv-lock family's session base — a scratch
    install root with a REAL venv, built ONCE per session THROUGH the
    module-level _build_probe_root helper (the ONE venv build site,
    pinned by TestProbeVenvBaseContract — the probe family's per-test
    venv build was the wall-time class this fixture does not re-open).
    Tests copy the base locally (writable: they plant dist-info and
    rewrite the lock)."""
    base_dir = tmp_path_factory.mktemp("hive-venv-lock-base")
    root = base_dir / "root"
    root.mkdir()
    if not _build_probe_root(root, with_system_site=False):
        pytest.skip("cannot create a scratch venv for the venv-lock test")
    return root


class TestDoctorVenvLock:
    """T1 (0.14): doctor cross-checks the installed venv against the
    venv lock (packaging/requirements.lock.txt) — the install's Python
    environment is a reproducible artifact, and drift is NAMED:
      (1) every applicable locked pin matches the venv's resolved
          version — a mismatch fails the check naming the package
          (locked version vs installed version)
      (2) a locked package missing from the venv is named
      (3) an install without a lock (pre-0.14) reports the resolved
          versions WITHOUT a FAIL (the no-fail contract for older
          installs)
    The venv comes from the probe family's session base (T2, 0.13 —
    ONE venv build site per session, pinned by
    TestProbeVenvBaseContract); each test COPIES the base locally
    because it writes (planted dist-info, rewritten lock). The planted
    dist-info is all the check reads — importlib.metadata (its
    resolver) reads the planted METADATA exactly as it reads a real
    install — so the tests stay hermetic (no network).
    Red against the pre-T1 doctor, which had no venv-lock check at all
    (a venv that re-resolved to different versions was undetectable).
    """

    install_dir = Path(__file__).resolve().parent.parent

    @staticmethod
    def _lock_text() -> str:
        return (TestDoctorVenvLock.install_dir / "packaging" /
                "requirements.lock.txt").read_text()

    @staticmethod
    def _lock_pins() -> dict:
        """{name: version} for the pins applicable to THIS interpreter
        (the tomli marker does not apply on 3.11+)."""
        from lib.hive_cli.commands.doctor import (_parse_venv_lock,
                                                  _lock_marker_applies)
        pins = {}
        for name, version, marker in _parse_venv_lock(TestDoctorVenvLock._lock_text()):
            if _lock_marker_applies(marker):
                pins[name] = version
        return pins

    @staticmethod
    def _scratch(tmp_path, base_root, with_lock=True):
        """A writable scratch install: a local copy of the session base
        (the probe family's scratch root — real venv + harness sources)
        plus the installed-set inputs (TestDoctorInstalledSet._install
        — required entries, the shared constant, the doctor's runtime
        TOMLs, the manifest) and the venv-lock test's state: the lock
        (when with_lock) and the planted dist-info in the venv's
        site-packages (every applicable pin)."""
        root = tmp_path / "install"
        shutil.copytree(base_root, root)
        TestDoctorInstalledSet._install(
            root, list(TestDoctorInstalledSet.REQUIRED) + ["MANIFEST.txt"])
        lock_file = root / "packaging" / "requirements.lock.txt"
        if with_lock:
            lock_file.write_text(TestDoctorVenvLock._lock_text())
        else:
            lock_file.unlink()
        site = (root / "venv" / "lib"
                / f"python{sys.version_info.major}.{sys.version_info.minor}"
                / "site-packages")
        site.mkdir(parents=True, exist_ok=True)
        for name, version in TestDoctorVenvLock._lock_pins().items():
            dist = site / f"{name}-{version}.dist-info"
            dist.mkdir(exist_ok=True)
            (dist / "METADATA").write_text(
                "Metadata-Version: 2.1\n"
                f"Name: {name}\n"
                f"Version: {version}\n")
        return root

    @staticmethod
    def _run_doctor(tmp_path, root: Path) -> subprocess.CompletedProcess:
        env = {**os.environ,
               "PYTHONPATH": str(TestDoctorVenvLock.install_dir / "lib")
               + os.pathsep + os.environ.get("PYTHONPATH", ""),
               "HIVE_INSTALL_DIR": str(root), "HIVE_ROOT": str(root)}
        return subprocess.run(
            [sys.executable, "-m", "hive_cli.main", "doctor", "--json",
             str(tmp_path / "proj")],
            capture_output=True, text=True, timeout=180, env=env)

    def _check(self, tmp_path, base_root, **kw) -> dict:
        root = self._scratch(tmp_path, base_root, **kw)
        r = self._run_doctor(tmp_path, root)
        assert r.returncode in (0, 1), r.stderr[-1000:]
        payload = json.loads(r.stdout)
        for c in payload["checks"]:
            if c["name"].startswith("venv lock"):
                return c
        raise AssertionError(
            "no venv-lock check in doctor output: "
            f"{[c['name'] for c in payload['checks']]}")

    def test_doctor_venv_lock_green_on_matching_venv(self, tmp_path,
                                                     _venv_lock_base):
        """(1) every applicable locked pin matches the venv's resolved
        version — the check passes and says so."""
        c = self._check(tmp_path, _venv_lock_base)
        assert c["ok"], f"matching venv must pass: {c}"
        assert "match" in c["detail"], c

    def test_doctor_venv_lock_drift_names_the_package(self, tmp_path,
                                                      _venv_lock_base):
        """(2) ONE package at a different version in the venv fails the
        check NAMING THE PACKAGE (locked version vs installed version)."""
        root = self._scratch(tmp_path, _venv_lock_base)
        planted = TestDoctorVenvLock._lock_pins()["fastapi"]
        site = (root / "venv" / "lib"
                / f"python{sys.version_info.major}.{sys.version_info.minor}"
                / "site-packages")
        shutil.rmtree(site / f"fastapi-{planted}.dist-info")
        wrong = site / "fastapi-9.9.9.dist-info"
        wrong.mkdir()
        (wrong / "METADATA").write_text(
            "Metadata-Version: 2.1\nName: fastapi\nVersion: 9.9.9\n")
        r = self._run_doctor(tmp_path, root)
        payload = json.loads(r.stdout)
        c = next(cc for cc in payload["checks"]
                 if cc["name"].startswith("venv lock"))
        assert not c["ok"], f"a drifted venv must fail: {c}"
        assert "fastapi" in c["detail"], \
            "the drifted package must be named: " + c["detail"]
        assert planted in c["detail"], \
            "the locked version must be named: " + c["detail"]
        assert "9.9.9" in c["detail"], \
            "the installed (wrong) version must be named: " + c["detail"]

    def test_doctor_venv_lock_missing_package_named(self, tmp_path,
                                                    _venv_lock_base):
        """(2b) a locked package ABSENT from the venv fails the check
        naming it."""
        root = self._scratch(tmp_path, _venv_lock_base)
        site = (root / "venv" / "lib"
                / f"python{sys.version_info.major}.{sys.version_info.minor}"
                / "site-packages")
        httpx_pin = TestDoctorVenvLock._lock_pins()["httpx"]
        shutil.rmtree(site / f"httpx-{httpx_pin}.dist-info")
        r = self._run_doctor(tmp_path, root)
        payload = json.loads(r.stdout)
        c = next(cc for cc in payload["checks"]
                 if cc["name"].startswith("venv lock"))
        assert not c["ok"], f"a missing locked package must fail: {c}"
        assert "httpx" in c["detail"], c

    def test_doctor_venv_lock_pre_lock_reports_and_passes(self, tmp_path,
                                                          _venv_lock_base):
        """(3) an install without a lock (pre-0.14) reports the resolved
        versions WITHOUT a FAIL — the no-fail contract for older
        installs."""
        c = self._check(tmp_path, _venv_lock_base, with_lock=False)
        assert c["ok"], "a pre-lock install must not fail the check: " + str(c)
        assert "no lock" in c["detail"], c
        pins = TestDoctorVenvLock._lock_pins()
        assert f"mcp={pins['mcp']}" in c["detail"], \
            "the pre-lock report must carry the resolved versions"


class TestDoctorUnconfiguredCrossReference:
    """T5 (0.14): the unconfigured-provider diagnostic cross-references
    D1's retired vars.

    An unconfigured provider and env.sh still exporting retired LLM
    vars is the exact situation the stale docs produced (they sent the
    operator to env.sh; doctor's D1 check tells them it was retired).
    The diagnostic carries the one-line fix for the actual situation
    — and D1's own warning still fires (behavior unchanged). Red
    before the cross-reference existed (the diagnostic was the
    generic two-way hint and ignored env.sh)."""

    install_dir = Path(__file__).resolve().parent.parent

    @classmethod
    def _run(cls, tmp_path):
        env = {k: v for k, v in os.environ.items()
               if k not in ("HIVE_CONFIG", "HIVE_PROVIDER", "HIVE_MODEL",
                            "HIVE_API_BASE", "HIVE_API_KEY", "HIVE_ENV_FILE")}
        env.update({
            "HOME": str(tmp_path),
            "HIVE_ROOT": str(cls.install_dir),
            "HIVE_INSTALL_DIR": str(cls.install_dir),
            "PYTHONPATH": str(cls.install_dir / "lib"),
        })
        return subprocess.run(
            [sys.executable, "-m", "hive_cli.main", "doctor"],
            capture_output=True, text=True, timeout=180, env=env,
            cwd=str(cls.install_dir))

    def test_unconfigured_with_retired_env_sh_vars_cross_references(self,
                                                                   tmp_path):
        envsh = tmp_path / ".config" / "hive" / "env.sh"
        envsh.parent.mkdir(parents=True)
        envsh.write_text("export HIVE_API_BASE=http://old.example\n"
                         "export HIVE_API_KEY=sk-old\n")
        r = self._run(tmp_path)
        assert r.returncode == 1, r.stdout  # unconfigured: expected fail
        assert ("env.sh still exports HIVE_API_BASE, HIVE_API_KEY "
                "(retired)") in r.stdout, r.stdout
        assert "[llm] in config.toml" in r.stdout, r.stdout
        # D1's own warning line still fires (behavior unchanged)
        assert "WARN: retired LLM var(s)" in r.stdout, r.stdout

    def test_unconfigured_without_env_sh_vars_gets_generic_hint(self,
                                                               tmp_path):
        r = self._run(tmp_path)
        assert r.returncode == 1, r.stdout
        assert "unconfigured" in r.stdout
        assert "env.sh still exports" not in r.stdout, r.stdout


class TestLlmDocEnvShConsistency:
    """T5 (0.14): one canonical LLM layer, and the docs agree with
    doctor's D1 check.

    config.toml's [llm] section is the canonical layer (every resolver
    reads it); the HIVE_* env vars are the one-shot override (shell
    export); env.sh carries daemon SHELL env (PATH, GITHUB_TOKEN) —
    NO LLM vars: D1 (pinned by TestDoctorEnvFile) declares the LLM
    var names in env.sh retired. The guard: no doc (AGENTS.md,
    share/docs/**) directs placing a name from D1's retired list into
    env.sh. The retired list is read from doctor.py — the single
    source. A fenced code block belongs to the line introducing it
    (the stale docs' pattern: the env.sh line above the export
    block). Red: the stale sentence fails the guard, naming the file."""

    install_dir = Path(__file__).resolve().parent.parent

    @classmethod
    def _docs(cls):
        docs = [cls.install_dir / "AGENTS.md"]
        docs.extend(sorted((cls.install_dir / "share" / "docs")
                           .glob("*.md")))
        return [d for d in docs if d.is_file()]

    @staticmethod
    def _blocks(text):
        """Paragraph blocks: contiguous non-blank lines; a fenced
        code block merges into the block introduced by the line
        immediately before it (blank lines in between allowed)."""
        raw_blocks, cur = [], []
        for line in text.splitlines():
            if line.strip() == "":
                if cur:
                    raw_blocks.append("\n".join(cur))
                    cur = []
            else:
                cur.append(line)
        if cur:
            raw_blocks.append("\n".join(cur))
        blocks, i, n = [], 0, len(raw_blocks)
        while i < n:
            b = raw_blocks[i]
            if (b.lstrip().startswith("```") and i > 0
                    and not raw_blocks[i - 1].lstrip().startswith("```")):
                blocks.append(raw_blocks[i - 1] + "\n" + b)
                i += 1
            else:
                blocks.append(b)
                i += 1
        return blocks

    @classmethod
    def _offenders(cls, text):
        from lib.hive_cli.commands.doctor import _ENV_FILE_RETIRED_VARS
        found = set()
        for block in cls._blocks(text):
            if "env.sh" not in block:
                continue
            for name in _ENV_FILE_RETIRED_VARS:
                if re.search(r"\b" + re.escape(name) + r"\b", block):
                    found.add(name)
        return sorted(found)

    def test_no_doc_directs_retired_vars_into_env_sh(self):
        bad = {}
        for d in self._docs():
            offs = self._offenders(d.read_text())
            if offs:
                bad[d.name] = offs
        assert not bad, (
            "docs direct D1's retired LLM vars into env.sh (doctor's "
            "D1 check warns on exactly these placements): " +
            "; ".join(f"{f}: {', '.join(n)}" for f, n in sorted(bad.items())))

    def test_guard_catches_the_stale_sentence(self):
        # Red test: the pre-0.14 AGENTS.md sentence is an offender —
        # every retired var it places in env.sh is named.
        stale = ("Environment (for daemons): `~/.config/hive/env.sh` — set the LLM\n"
                 "endpoint config there (`HIVE_API_BASE`, `HIVE_API_KEY`, `HIVE_MODEL`)")
        assert self._offenders(stale) == [
            "HIVE_API_BASE", "HIVE_API_KEY", "HIVE_MODEL"]
        # ...and the stale LLM-PROVIDERS.md pattern (env.sh line above
        # the export fence) is caught by the fence-merge rule.
        stale2 = ("Equivalently via env (e.g. in `~/.config/hive/env.sh` for daemons):\n"
                  "\n"
                  "```bash\n"
                  "export HIVE_API_BASE=http://old.example\n"
                  "```")
        assert "HIVE_API_BASE" in self._offenders(stale2)
