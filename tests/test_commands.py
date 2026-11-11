"""Tests for hive_cli.commands — command handlers."""
import sys
import os
import json
import re
import shutil
import subprocess
import tomllib  # stdlib (3.11+; the 0.15 floor)
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
        import tomllib  # stdlib (3.11+; the 0.15 floor)
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
        # 0.18 T1: the human table is the default face; the machine
        # marking moved to `show --json` (same top-level contract).
        self._write_user_config(tmp_path, '[llms]\nprovder = "openai"\n')
        r = self._run(tmp_path, "config", "show", "--json")
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert "llms.provder" in data.get("_unknown_keys", []), \
            "unknown keys must be marked in the show --json output"
        assert "warning: unknown key llms.provder" in r.stderr, \
            "the unknown-key warnings stay on stderr on every face"

    def test_config_show_clean_has_no_unknowns(self, tmp_path):
        self._write_user_config(tmp_path, '[llm]\nprovider = "openai"\n')
        r = self._run(tmp_path, "config", "show", "--json")
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


class TestConfigMasking:
    """T1 (0.18): the config command never prints the key — the one
    leak in the system (doctor, `llm test`, and the dashboard all name
    only the source) closed on every face. A seeded user config with a
    fake key: the key's bytes appear NOWHERE in the stdout of `config
    show`, `config show --json`, or `config get llm.api_key` (rc 0 +
    the masked placeholder present). Non-secret `get` is byte-
    identical to 0.17. The human face names the layer that WON per
    key; `--json` is the 0.17.0 document with only the mask.
    Driven against a scratch HOME + HIVE_CONFIG (the one spelling
    doctor and the publish runner read)."""

    KEY = "sk-test-fake-key-1"
    install_dir = Path(__file__).resolve().parent.parent

    @staticmethod
    def _run(home, *args, extra=None):
        env = {**os.environ, "HOME": str(home),
               "HIVE_ROOT": str(TestConfigMasking.install_dir),
               "PYTHONPATH": str(TestConfigMasking.install_dir / "lib")
               + os.pathsep + os.environ.get("PYTHONPATH", "")}
        env.update(extra or {})
        return subprocess.run(
            [sys.executable, "-m", "hive_cli.main", "config", *args],
            capture_output=True, text=True, env=env,
            cwd=str(TestConfigMasking.install_dir), timeout=120,
        )

    @staticmethod
    def _seed(home, text):
        """The HIVE_CONFIG override — a seeded user config with the fake key."""
        cfg = home / "cfg" / "config.toml"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(text)
        return cfg

    @staticmethod
    def _row(ln: str):
        """A table line → (section, key, value, source). Columns are
        separated by 2+ spaces; the VALUE cell re-joins any split
        middle parts (values with embedded double spaces)."""
        parts = re.split(r"\s{2,}", ln.strip())
        if len(parts) < 4:
            return None
        return (parts[0], parts[1], "  ".join(parts[2:-1]), parts[-1])

    @staticmethod
    def _rows(r: subprocess.CompletedProcess):
        """The table lines of a `config show` run, parsed."""
        rows = []
        for ln in r.stdout.splitlines()[1:]:
            row = TestConfigMasking._row(ln)
            if row:
                rows.append(row)
        return rows

    SEED = ('[llm]\nprovider = "openai"\nmodel = "gpt-test"\n'
            'api_key = "sk-test-fake-key-1"\n')

    def test_key_never_reaches_stdout(self, tmp_path):
        cfg = self._seed(tmp_path, self.SEED)
        extra = {"HIVE_CONFIG": str(cfg)}
        show = self._run(tmp_path, "show", extra=extra)
        show_json = self._run(tmp_path, "show", "--json", extra=extra)
        get = self._run(tmp_path, "get", "llm.api_key", extra=extra)
        for r in (show, show_json, get):
            assert r.returncode == 0, r.stderr
            assert self.KEY not in r.stdout, \
                "the key's bytes must not appear on any face — " \
                f"face: {r.args}"
        # the human faces print the raw placeholder
        assert "<set — masked>" in show.stdout
        assert "<set — masked>" in get.stdout
        assert get.stdout.strip() == "<set — masked>"
        # --json carries the mask as the value (the document's bytes keep
        # 0.17's json.dumps escaping — assert on the parsed value)
        assert json.loads(show_json.stdout)["llm"]["api_key"] == "<set — masked>"

    def test_get_non_secret_byte_identical(self, tmp_path):
        cfg = self._seed(tmp_path, self.SEED)
        extra = {"HIVE_CONFIG": str(cfg)}
        r = self._run(tmp_path, "get", "llm.provider", extra=extra)
        assert r.returncode == 0, r.stderr
        assert r.stdout == "openai\n", "non-secret keys print exactly as 0.17"
        r = self._run(tmp_path, "get", "mcp.port", extra=extra)
        assert r.returncode == 0, r.stderr
        assert r.stdout == "7891\n", "an un-overridden default prints exactly as 0.17"

    def test_get_unset_unchanged(self, tmp_path):
        r = self._run(tmp_path, "get", "nosuch.key")
        assert r.returncode == 1
        assert "(unset: nosuch.key)" in r.stderr
        assert r.stdout == ""

    def test_get_empty_secret_prints_empty_not_masked(self, tmp_path):
        # An empty value is not a secret — it prints as set to empty,
        # exactly as 0.17 did (masking a hole would be a lie).
        cfg = self._seed(tmp_path, '[llm]\napi_key = ""\n')
        r = self._run(tmp_path, "get", "llm.api_key",
                      extra={"HIVE_CONFIG": str(cfg)})
        assert r.returncode == 0, r.stderr
        assert r.stdout == "\n"

    def test_show_human_table_names_source_per_key(self, tmp_path):
        cfg = self._seed(tmp_path, self.SEED)
        r = self._run(tmp_path, "show", extra={"HIVE_CONFIG": str(cfg)})
        assert r.returncode == 0, r.stderr
        lines = r.stdout.splitlines()
        assert lines[0].startswith("SECTION"), \
            "the human face is the table, not the JSON dump"
        rows = self._rows(r)
        # a user-config override is 'user config'
        prov = [row for row in rows if row[1] == "provider"]
        assert prov and prov[0][3] == "user config", \
            "an overridden key must name the user config as its source"
        assert prov[0][2] == "openai"
        # an un-overridden key is 'default'
        port = [row for row in rows if row[0] == "mcp" and row[1] == "port"]
        assert port and port[0][3] == "default" and port[0][2] == "7891", \
            "an un-overridden key must name the install default"
        # the secret's value cell is the mask, not the key
        key_rows = [row for row in rows if row[1] == "api_key"]
        assert key_rows and key_rows[0][2] == "<set — masked>"
        assert self.KEY not in r.stdout

    def test_show_env_override_named_per_key(self, tmp_path):
        # With HIVE_MODEL exported, llm.model's SOURCE names the env var
        # and the env value is shown (env wins over both file layers).
        cfg = self._seed(tmp_path, self.SEED)
        r = self._run(tmp_path, "show",
                      extra={"HIVE_CONFIG": str(cfg),
                             "HIVE_MODEL": "env-model-xyz"})
        assert r.returncode == 0, r.stderr
        rows = self._rows(r)
        model_rows = [row for row in rows
                      if row[0] == "llm" and row[1] == "model"]
        assert model_rows, "the llm.model row must be present"
        assert model_rows[0][2] == "env-model-xyz", \
            "the env value must be the shown value"
        assert model_rows[0][3] == "env HIVE_MODEL", \
            "the env var must be named as the winning layer"
        # and a non-env key keeps its file-layer source
        prov_rows = [row for row in rows
                     if row[0] == "llm" and row[1] == "provider"]
        assert prov_rows and prov_rows[0][3] == "user config"

    def test_show_env_secret_value_is_masked(self, tmp_path):
        # A secret's ENV value wins the layer race — and is masked like
        # every other layer's: the bytes never reach stdout.
        env_key = "sk-env-fake-key-1"
        cfg = self._seed(tmp_path, self.SEED)
        r = self._run(tmp_path, "show",
                      extra={"HIVE_CONFIG": str(cfg),
                             "HIVE_API_KEY": env_key})
        assert r.returncode == 0, r.stderr
        assert env_key not in r.stdout, \
            "the env layer's secret value must be masked too"
        key_rows = [row for row in self._rows(r)
                    if row[0] == "llm" and row[1] == "api_key"]
        assert key_rows and key_rows[0][3] == "env HIVE_API_KEY"
        assert key_rows[0][2] == "<set — masked>"

    def test_json_golden_is_the_017_document_with_the_mask(self, tmp_path):
        # --json equals the 0.17.0 output with the key masked —
        # nothing else in the document moves.
        cfg = self._seed(tmp_path, self.SEED)
        r = self._run(tmp_path, "show", "--json",
                      extra={"HIVE_CONFIG": str(cfg)})
        assert r.returncode == 0, r.stderr
        got = json.loads(r.stdout)
        defaults = tomllib.loads(
            (self.install_dir / "etc" / "config.defaults.toml").read_text())
        user = tomllib.loads(cfg.read_text())
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(defaults.get(k), dict):
                defaults[k].update(v)
            else:
                defaults[k] = v
        defaults["llm"]["api_key"] = "<set — masked>"
        assert got == defaults, \
            "the --json document must be the 0.17 merged config with " \
            "ONLY the mask — no key added, renamed, or dropped"

    def test_json_unknown_keys_stay_top_level_and_masked(self, tmp_path):
        # The A1 unknown-key marking stays top-level; the mask applies
        # in the same document.
        cfg = self._seed(
            tmp_path, self.SEED + '\n[llms]\nprovder = "openai"\n')
        r = self._run(tmp_path, "show", "--json",
                      extra={"HIVE_CONFIG": str(cfg)})
        assert r.returncode == 0, r.stderr
        got = json.loads(r.stdout)
        assert got.get("_unknown_keys") == ["llms.provder"], \
            "the unknown-key list stays top-level in --json"
        assert got["llm"]["api_key"] == "<set — masked>"
        assert self.KEY not in r.stdout
        assert "warning: unknown key llms.provder" in r.stderr, \
            "the unknown-key warnings stay on stderr"


class TestRosterCommand:
    """T2 (0.18): `hive roster [dept] [--active] [--json]` — who is
    working on what, in the terminal. Read-only and CLI-only: it rides
    the existing roster_get MCP tool (the same script path,
    roster-update.sh list, rebuild before read) — no wire change, no
    agent-side diff. The human face is the NAME / ROLE / DEPT / STATUS
    / TICKET / LAST ACTIVE table (active first, then claimed, then
    inactive) with the count line; --json is the .agents array exactly
    as roster_get returns it. The empty states are named: no .company
    → the init hint (rc 1); rebuild failed → the named error (rc 1 —
    today: silent `[]`, rc 0); derived-but-empty → the count-0 listing
    (rc 0); a named-missing dept → exit 1 + the known-dept list.
    End-to-end through the real CLI + stdio MCP server + bash scripts
    (dead HIVE_MCP_PORT forces the stdio transport)."""

    DEPTS = ("finance", "hr", "it", "legal", "marketing", "rd")

    @staticmethod
    def _cli(cwd: Path, company_dir: str | None, *argv: str,
             extra: dict | None = None) -> subprocess.CompletedProcess:
        env = {**os.environ,
               "PYTHONPATH": str(Path(__file__).resolve().parent.parent / "lib"),
               # dead port — force the stdio MCP fallback
               "HIVE_MCP_PORT": "1"}
        if company_dir is not None:
            env["COMPANY_DIR"] = company_dir
        else:
            env.pop("COMPANY_DIR", None)
        env.update(extra or {})
        return subprocess.run(
            [sys.executable, "-m", "hive_cli.main", *argv],
            capture_output=True, text=True, timeout=180,
            cwd=str(cwd), env=env)

    @staticmethod
    def _workspace(root: Path, name: str, *, logs: bool = False,
                   ticket: bool = False) -> Path:
        cd = root / ".company"
        (cd / "logs").mkdir(parents=True, exist_ok=True)
        (cd / "org-chart.json").write_text(json.dumps({
            "departments": {d: {"name": d.title()} for d in
                            TestRosterCommand.DEPTS},
            "created_at": "2026-11-10T00:00:00Z"}))
        (cd / "manifest.json").write_text(
            json.dumps({"project_name": name, "mode": "build",
                        "auto_mode": False}))
        if logs:
            # A fresh VP log (mtime = now): the rebuild reads it as
            # active (inside the 30-minute window).
            (cd / "logs" / "vp-rd.log").write_text("[cycle] rd cycle 1\n")
        if ticket:
            # An in-progress ticket with a paper claim (no live spawn
            # record → the IC row reads 'claimed').
            base = cd / "departments" / "rd" / "tickets" / "in-progress"
            base.mkdir(parents=True, exist_ok=True)
            (base / "RD-7777.json").write_text(json.dumps({
                "id": "RD-7777", "type": "feature",
                "title": "Implement the roster command",
                "description": "The terminal face of the roster.",
                "created_by": "user", "source": "manual",
                "priority": "high", "status": "in-progress",
                "created_at": "2026-11-10T09:00:00Z",
                "updated_at": "2026-11-11T09:00:00Z",
                "assignee": "Eng-42", "comments": [],
                "gates_passed": False, "verified_by": "",
                "branch": "feature/RD-7777"}))
        return cd

    @pytest.fixture(scope="class")
    def running(self, tmp_path_factory):
        """A company that IS running: fresh VP-rd log + claimed IC."""
        root = tmp_path_factory.mktemp("roster-running")
        return self._workspace(root, "roster-running",
                               logs=True, ticket=True)

    @pytest.fixture(scope="class")
    def never_run(self, tmp_path_factory):
        """A fresh, never-run workspace: org-chart only."""
        root = tmp_path_factory.mktemp("roster-never")
        return self._workspace(root, "roster-never")

    # -- the running workspace -------------------------------------------

    def test_running_workspace_row_on_screen(self, running):
        r = self._cli(running.parent, str(running), "roster")
        assert r.returncode == 0, r.stderr
        out = r.stdout
        # The IC row: name, dept, the ticket id, and the claimed state
        assert "Eng-42" in out, "the assignee's name must be on screen"
        assert "RD-7777" in out, "the ticket id must be on screen"
        assert "claimed" in out
        # The fresh VP log: an active row for rd
        rd_rows = [ln for ln in out.splitlines()
                   if ln.startswith("VP-RD ")]
        assert rd_rows and "active" in rd_rows[0], \
            "a fresh VP log must read as an active row"
        # The envelope keys are NOT the human face
        for key in ('"ok"', '"data"', 'exit_code'):
            assert key not in out
        # Count line at the end: 6 VPs + 1 IC, one active
        assert out.strip().splitlines()[-1] == "7 agent(s) — 1 active"
        # Active rows first, then claimed, then inactive
        lines = out.splitlines()
        assert lines.index([l for l in lines if l.startswith("VP-RD ")][0]) \
            < lines.index([l for l in lines if l.startswith("Eng-42")][0]) \
            < lines.index([l for l in lines if l.startswith("VP-FINANCE")][0])

    def test_json_equals_roster_get_document(self, running):
        r = self._cli(running.parent, str(running), "roster", "--json")
        assert r.returncode == 0, r.stderr
        got = json.loads(r.stdout)
        # The MCP side for the same call (the golden)
        tool = self._cli(running.parent, str(running), "tool", "roster_get",
                         "--arg", f"company_dir={running}")
        assert tool.returncode == 0, tool.stderr
        env = json.loads(tool.stdout)
        assert env["ok"] is True
        assert got == env["data"], \
            "--json must be the .agents array exactly as roster_get " \
            "returns it — one byte-identical contract"
        assert isinstance(got, list) and len(got) == 7

    def test_filters_active_and_dept(self, running):
        r = self._cli(running.parent, str(running), "roster", "--active")
        assert r.returncode == 0, r.stderr
        assert "VP-RD" in r.stdout
        assert "VP-MARKETING" not in r.stdout, \
            "--active must drop the inactive rows"
        assert r.stdout.strip().splitlines()[-1] == "1 agent(s) — 1 active"

        r = self._cli(running.parent, str(running), "roster", "marketing")
        assert r.returncode == 0, r.stderr
        assert "VP-MARKETING" in r.stdout
        assert "VP-RD" not in r.stdout
        assert "Eng-42" not in r.stdout
        assert r.stdout.strip().splitlines()[-1] == "1 agent(s) — 0 active"

        # --dept on the JSON face filters as the MCP tool does
        r = self._cli(running.parent, str(running), "roster", "rd", "--json")
        got = json.loads(r.stdout)
        assert {a["department"] for a in got} == {"rd"}

    def test_named_missing_dept_is_an_error(self, running):
        r = self._cli(running.parent, str(running), "roster", "nosuchdept")
        assert r.returncode == 1, \
            "a named department that does not exist is an error"
        assert "Department 'nosuchdept' not found" in r.stderr
        # the known-dept list (the 0.17 T2 contract, same family)
        assert "available:" in r.stderr
        for d in ("finance", "marketing", "rd"):
            assert d in r.stderr

    # -- the never-run workspace ------------------------------------------

    def test_never_run_workspace_lists_inactive_vps(self, never_run):
        r = self._cli(never_run.parent, str(never_run), "roster")
        assert r.returncode == 0, r.stderr
        out = r.stdout
        for d in self.DEPTS:
            assert f"VP-{d.upper()}" in out, \
                f"the {d} VP row must be on screen"
        assert "active" not in out.split("\n")[1:], \
            "a never-run workspace has no active rows"
        # the count line says the state: the company is not running
        assert out.strip().splitlines()[-1] == "6 agent(s) — 0 active"

    # -- the named empty states -------------------------------------------

    def test_no_workspace_says_run_init(self, tmp_path):
        r = self._cli(tmp_path, None, "roster")
        assert r.returncode == 1, \
            "a no-workspace dir must not land on the silent `[]`, rc 0"
        assert "hive init" in r.stderr, \
            "the init hint must name the next step"

    @staticmethod
    def _no_jq_path(tmp: Path) -> str:
        """A PATH with every tool the rebuild path needs EXCEPT jq —
        the deterministic forced rebuild failure (the script's `||
        true` swallows it exactly as in production)."""
        bindir = tmp / "nojq-bin"
        bindir.mkdir(exist_ok=True)
        for name in ("bash", "sh", "mkdir", "date", "stat", "git", "cat",
                     "touch", "rm", "mv", "cp", "ln", "sort", "sed", "awk",
                     "grep", "tr", "head", "tail", "wc", "basename",
                     "dirname", "mktemp", "flock", "readlink", "python3"):
            src = shutil.which(name)
            if src and not (bindir / name).exists():
                (bindir / name).symlink_to(src)
        assert not (bindir / "jq").exists()
        return str(bindir)

    def test_forced_rebuild_failure_is_named(self, tmp_path):
        cd = self._workspace(tmp_path, "roster-broken")
        r = self._cli(tmp_path, str(cd), "roster",
                      extra={"PATH": self._no_jq_path(tmp_path)})
        assert r.returncode == 1, \
            "a failed rebuild must not land on the silent `[]`, rc 0"
        assert "roster unavailable" in r.stderr, \
            "the rebuild failure must be named, not indistinguishable " \
            "from 'no agents'"
        assert "hive doctor" in r.stderr
        assert not (cd / "roster.json").exists()


class TestChatSendDefaults:
    """T3 (0.18): `hive chat send` takes the channel and the message —
    saying hello to the company is one argument. author defaults to
    the OS user running the command, department to the channel's own
    department (dept-rd → rd; a named channel → itself); the MCP
    payload shape (and the file content) is unchanged, so the test
    asserts on the channel file — the commit point. The pinned docs
    line moved in the same commit (test_dashboard.py)."""

    @staticmethod
    def _cli(cwd: Path, company_dir: str, *argv: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "hive_cli.main", *argv],
            capture_output=True, text=True, timeout=120,
            cwd=str(cwd),
            env={**os.environ,
                 "PYTHONPATH": str(Path(__file__).resolve()
                                   .parent.parent / "lib"),
                 # dead port — force the stdio MCP fallback
                 "HIVE_MCP_PORT": "1",
                 "COMPANY_DIR": company_dir})

    @pytest.fixture(scope="class")
    def seeded(self, tmp_path_factory):
        root = tmp_path_factory.mktemp("chat-send-defaults")
        (root / ".company").mkdir()
        return root

    def test_one_arg_form_posts_as_os_user_channel_dept(self, seeded):
        import getpass
        cd = seeded / ".company"
        r = self._cli(seeded, str(cd), "chat", "send", "general",
                      "hello world from the terminal")
        assert r.returncode == 0, r.stderr
        assert "✓ sent to general" in r.stdout
        text = (cd / "chat" / "general.md").read_text()
        # the commit point: the file carries the OS user and the
        # channel's own department
        assert f"] {getpass.getuser()} (general)**" in text
        assert "hello world from the terminal" in text

    def test_dept_channel_uses_the_department_name(self, seeded):
        cd = seeded / ".company"
        r = self._cli(seeded, str(cd), "chat", "send", "dept-rd", "hi team")
        assert r.returncode == 0, r.stderr
        text = (cd / "chat" / "dept-rd.md").read_text()
        assert f" (rd)**" in text
        assert "hi team" in text

    def test_flags_override_the_defaults(self, seeded):
        cd = seeded / ".company"
        r = self._cli(seeded, str(cd), "chat", "send", "general", "x",
                      "--author", "Bob", "--dept", "it")
        assert r.returncode == 0, r.stderr
        text = (cd / "chat" / "general.md").read_text()
        assert "Bob (it)**" in text

    def test_send_without_a_message_names_its_error(self, seeded):
        # a channel no earlier test touched — the send must not create
        # the channel file when it refuses
        cd = seeded / ".company"
        r = self._cli(seeded, str(cd), "chat", "send", "leadership")
        assert r.returncode == 1
        assert "a message is required" in r.stderr
        assert "usage: hive chat send" in r.stderr
        assert not (cd / "chat" / "leadership.md").exists()

    def test_json_envelope_shape_unchanged(self, seeded):
        cd = seeded / ".company"
        r = self._cli(seeded, str(cd), "chat", "send", "incidents",
                      "all clear", "--json")
        assert r.returncode == 0, r.stderr
        env = json.loads(r.stdout)
        # chat-send.sh prints a human line (not JSON) — the envelope
        # carries it on stdout, exactly as in 0.17
        assert set(env.keys()) == {"ok", "exit_code", "stderr", "stdout"}
        assert env["ok"] is True
        assert "Message sent to #incidents" in env["stdout"]


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
                        "release", "status", "--json")  # T1: machine face
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

    def test_doctor_venv_exact_set_green_on_released_venv(self, tmp_path,
                                                          _venv_lock_base):
        """T1 (0.15): a venv holding EXACTLY the released set (the
        planted lock pins + the venv's own tooling) passes the
        exact-set check — one list with the install's prune
        (lib/hive_cli/commands/venv_prune.py)."""
        root = self._scratch(tmp_path, _venv_lock_base)
        r = self._run_doctor(tmp_path, root)
        payload = json.loads(r.stdout)
        c = next(cc for cc in payload["checks"]
                 if cc["name"].startswith("venv exact set"))
        assert c["ok"], f"a released venv must pass the exact-set check: {c}"
        assert "exact" in c["detail"], c

    def test_doctor_venv_exact_set_names_outside_names(self, tmp_path,
                                                        _venv_lock_base):
        """T1 (0.15): a venv holding a name OUTSIDE the released set
        (a hand pip install) fails the exact-set check NAMING THE
        PACKAGE, with the fix (re-run the install / pip uninstall)."""
        root = self._scratch(tmp_path, _venv_lock_base)
        site = (root / "venv" / "lib"
                / f"python{sys.version_info.major}.{sys.version_info.minor}"
                / "site-packages")
        dist = site / "hand-installed-extra-1.0.dist-info"
        dist.mkdir()
        (dist / "METADATA").write_text(
            "Metadata-Version: 2.1\nName: hand-installed-extra\nVersion: 1.0\n")
        r = self._run_doctor(tmp_path, root)
        payload = json.loads(r.stdout)
        c = next(cc for cc in payload["checks"]
                 if cc["name"].startswith("venv exact set"))
        assert not c["ok"], f"a venv with an outside name must fail: {c}"
        assert "hand-installed-extra" in c["detail"], c
        assert "pip uninstall" in c["detail"], \
            "the fix hint must name pip uninstall: " + c["detail"]


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


class TestTicketHumanFace:
    """T1 (0.17): `hive ticket` speaks human by default; --json is the
    0.16.0 envelope byte-for-byte — the machine contract does not move.

    End-to-end through the real CLI + stdio MCP server + bash scripts
    on a seeded workspace (the dead HIVE_MCP_PORT forces the stdio
    transport, so a live HTTP server can never answer). Tests run in
    definition order: the read paths first (3 seeded tickets), then the
    mutations (create -> move -> claim -> comment on the new ticket,
    approve on the seeded in-review one)."""

    # The documented ticket-list.sh --json summary schema — the golden
    # pin: a field added/renamed upstream fails the --json contract.
    SUMMARY_KEYS = {"id", "title", "status", "type", "priority", "assignee",
                    "gates_passed", "verified_by", "skip_tdd_red",
                    "comment_count", "last_comment"}

    @staticmethod
    def _cli(cwd: Path, company_dir: str, *argv: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "hive_cli.main", *argv],
            capture_output=True, text=True, timeout=120,
            cwd=str(cwd),
            env={**os.environ,
                 "PYTHONPATH": str(Path(__file__).resolve()
                                   .parent.parent / "lib"),
                 # dead port — force the stdio MCP fallback
                 "HIVE_MCP_PORT": "1",
                 "COMPANY_DIR": company_dir,
                 "DASHBOARD_PORT": "3456"})

    @staticmethod
    def _ticket(tid, title, status, pri, typ, assignee=None, **kw):
        t = {
            "id": tid, "type": typ, "title": title,
            "description": f"Description of {title}.",
            "created_by": "user", "filed_by_department": "rd",
            "source": "manual", "priority": pri, "status": status,
            "created_at": "2026-11-10T09:00:00Z",
            "updated_at": "2026-11-11T09:00:00Z",
            "assignee": assignee or {},
            "comments": [],
            "gates_passed": False, "verified_by": "", "skip_tdd_red": False,
        }
        t.update(kw)
        return t

    @pytest.fixture(scope="class")
    def seeded(self, tmp_path_factory):
        root = tmp_path_factory.mktemp("ticket-human")
        cd = root / ".company"
        cd.mkdir()
        (cd / "org-chart.json").write_text(json.dumps({
            "departments": {"rd": {"name": "R&D", "reporting_to": None},
                            "it": {"name": "IT", "reporting_to": None}},
            "created_at": "2026-11-10T00:00:00Z"}))
        (cd / "manifest.json").write_text(json.dumps({
            "project_name": "ticket-human", "mode": "build", "auto_mode": False}))
        base = cd / "departments" / "rd" / "tickets"
        for st in ("backlog", "sprint-current", "in-progress", "in-review", "done"):
            (base / st).mkdir(parents=True)
        (base / "backlog" / "RD-0102.json").write_text(json.dumps(
            self._ticket("RD-0102", "Fix nav overflow", "backlog", "medium", "bug"),
            indent=2))
        (base / "in-progress" / "RD-0101.json").write_text(json.dumps(
            self._ticket("RD-0101", "Add login flow", "in-progress", "high", "feature",
                         assignee={"agent_id": "ic-RD-0101", "name": "Eng-1",
                                   "role": "engineer"}),
            indent=2))
        (base / "in-review" / "RD-0103.json").write_text(json.dumps(
            self._ticket("RD-0103", "Upgrade toolchain", "in-review", "critical",
                         "infrastructure", gates_passed=True,
                         gates_passed_at="2026-11-11T08:00:00Z"),
            indent=2))
        return root

    # -- read paths (the 3 seeded tickets) --------------------------------

    def test_list_human_is_the_answer_not_the_envelope(self, seeded):
        cd = seeded / ".company"
        r = self._cli(seeded, str(cd), "ticket", "list", "rd")
        assert r.returncode == 0, r.stderr
        out = r.stdout
        # The answer is on the screen: title, status, assignee
        assert "Add login flow" in out and "in-progress" in out and "Eng-1" in out
        # The envelope keys are NOT
        for key in ("exit_code", "stderr", '"ok"', '"data"'):
            assert key not in out
        # Count line at the end, board order (backlog before in-progress)
        assert out.strip().splitlines()[-1] == "3 ticket(s) in rd"
        assert out.index("RD-0102") < out.index("RD-0101") < out.index("RD-0103")

    def test_list_json_is_the_016_envelope(self, seeded):
        cd = seeded / ".company"
        r = self._cli(seeded, str(cd), "ticket", "list", "rd", "--json")
        assert r.returncode == 0, r.stderr
        env = json.loads(r.stdout)
        assert set(env.keys()) == {"ok", "exit_code", "stderr", "data"}
        assert env["ok"] is True and env["exit_code"] == 0
        data = env["data"]
        # The per-dept document, byte-shape: the 0.16.0 fields, no more
        assert set(data.keys()) == {"department", "count", "tickets", "mode"}
        assert data["department"] == "rd" and data["count"] == 3
        assert data["mode"] == "summary"
        ids = {t["id"] for t in data["tickets"]}
        assert ids == {"RD-0101", "RD-0102", "RD-0103"}
        for t in data["tickets"]:
            assert set(t.keys()) == self.SUMMARY_KEYS

    def test_list_with_status_filter(self, seeded):
        cd = seeded / ".company"
        r = self._cli(seeded, str(cd), "ticket", "list", "rd", "backlog")
        assert r.returncode == 0, r.stderr
        assert "RD-0102" in r.stdout and "RD-0101" not in r.stdout
        assert r.stdout.strip().splitlines()[-1] == "1 ticket(s) in rd"

    def test_get_human_card(self, seeded):
        cd = seeded / ".company"
        r = self._cli(seeded, str(cd), "ticket", "get", "rd", "RD-0101")
        assert r.returncode == 0, r.stderr
        out = r.stdout
        assert out.splitlines()[0] == "RD-0101 — Add login flow"
        assert "status:    in-progress" in out
        assert "priority:  high" in out
        assert "assignee:  Eng-1" in out
        assert "Description:" in out
        for key in ("exit_code", "stderr", '"ok"'):
            assert key not in out

    def test_get_json_is_the_full_document(self, seeded):
        cd = seeded / ".company"
        r = self._cli(seeded, str(cd), "ticket", "get", "rd", "RD-0101", "--json")
        assert r.returncode == 0, r.stderr
        env = json.loads(r.stdout)
        assert set(env.keys()) == {"ok", "exit_code", "stderr", "data"}
        assert env["data"]["id"] == "RD-0101"
        assert env["data"]["description"] == "Description of Add login flow."

    def test_get_missing_is_an_error_not_a_silent_empty(self, seeded):
        cd = seeded / ".company"
        r = self._cli(seeded, str(cd), "ticket", "get", "rd", "RD-9999")
        assert r.returncode == 1
        assert "not found" in r.stderr
        r = self._cli(seeded, str(cd), "ticket", "get", "rd", "RD-9999", "--json")
        assert r.returncode == 1
        env = json.loads(r.stdout)
        assert env["ok"] is False
        assert "not found" in env["stderr"]

    # -- mutations (in definition order) ----------------------------------

    def test_create_human_line_and_hint(self, seeded):
        cd = seeded / ".company"
        r = self._cli(seeded, str(cd), "ticket", "create", "rd", "feature", "high",
                      "New ticket from the human face", "Needs doing",
                      "--created-by", "user", "--source", "manual")
        assert r.returncode == 0, r.stderr
        lines = r.stdout.strip().splitlines()
        assert lines[0] == "✓ RD-0104 (feature, high) in rd — backlog"
        assert lines[1].strip() == "watch it at http://localhost:3456/"
        assert (cd / "departments" / "rd" / "tickets" / "backlog"
                / "RD-0104.json").is_file()

    def test_create_json_envelope(self, seeded):
        cd = seeded / ".company"
        r = self._cli(seeded, str(cd), "ticket", "create", "rd", "bug", "low",
                      "Second human ticket", "Also needs doing",
                      "--json")
        assert r.returncode == 0, r.stderr
        env = json.loads(r.stdout)
        assert set(env.keys()) == {"ok", "exit_code", "stderr", "stdout"}
        assert env["ok"] is True
        assert env["stdout"].strip() == "RD-0105"

    def test_move_human_resulting_state(self, seeded):
        cd = seeded / ".company"
        r = self._cli(seeded, str(cd), "ticket", "move", "rd", "RD-0104",
                      "sprint-current", "into the sprint", "user")
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip().splitlines()[0] == \
            "✓ Moved RD-0104: backlog -> sprint-current"

    def test_move_json_envelope(self, seeded):
        cd = seeded / ".company"
        r = self._cli(seeded, str(cd), "ticket", "move", "rd", "RD-0105",
                      "sprint-current", "into the sprint", "user", "--json")
        assert r.returncode == 0, r.stderr
        env = json.loads(r.stdout)
        assert set(env.keys()) == {"ok", "exit_code", "stderr", "stdout"}
        assert "Moved RD-0105" in env["stdout"]

    def test_claim_human(self, seeded):
        cd = seeded / ".company"
        r = self._cli(seeded, str(cd), "ticket", "claim", "rd", "RD-0104",
                      "engineer", "ic-RD-0104", "Eng-4")
        assert r.returncode == 0, r.stderr
        assert "Claimed RD-0104 by Eng-4 (engineer)" in r.stdout
        assert "in-progress" in r.stdout

    def test_comment_human(self, seeded):
        cd = seeded / ".company"
        r = self._cli(seeded, str(cd), "ticket", "comment", "rd", "RD-0104",
                      "user", "on it")
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == "✓ Comment added to RD-0104 by user"

    def test_approve_human_and_json(self, seeded):
        cd = seeded / ".company"
        r = self._cli(seeded, str(cd), "ticket", "approve", "rd", "RD-0103",
                      "Verification-IC",
                      "Verified: toolchain upgrades applied and the suite passes")
        assert r.returncode == 0, r.stderr
        assert "RD-0103 approved by Verification-IC" in r.stdout
        # The move to done committed (gates_passed was seeded true)
        assert (cd / "departments" / "rd" / "tickets" / "done"
                / "RD-0103.json").is_file()

    def test_list_after_mutations_still_reads(self, seeded):
        cd = seeded / ".company"
        r = self._cli(seeded, str(cd), "ticket", "list", "rd")
        assert r.returncode == 0, r.stderr
        assert "5 ticket(s) in rd" in r.stdout


class TestChatHumanFace:
    """T1 (0.17): `hive chat` — the transcript by default; --json is
    the 0.16.0 envelope. `hive chat read --json` was a no-op in 0.16
    (advertised in the parser, never consulted) because the default
    already WAS the raw envelope — it is the machine face now, and the
    default is human. Tests run in definition order: reads before the
    send (which appends to the channel)."""

    CHANNEL_MD = """# Channel: general

---
**[2026-11-11T09:30:00] user (general)**
sprint 3 starting

---
**[2026-11-11T09:45:00] Eng-1 (rd)**
on it
"""

    @staticmethod
    def _cli(cwd: Path, company_dir: str, *argv: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "hive_cli.main", *argv],
            capture_output=True, text=True, timeout=120,
            cwd=str(cwd),
            env={**os.environ,
                 "PYTHONPATH": str(Path(__file__).resolve()
                                   .parent.parent / "lib"),
                 "HIVE_MCP_PORT": "1",
                 "COMPANY_DIR": company_dir})

    @pytest.fixture(scope="class")
    def seeded(self, tmp_path_factory):
        root = tmp_path_factory.mktemp("chat-human")
        cd = root / ".company"
        (cd / "chat").mkdir(parents=True)
        (cd / "org-chart.json").write_text(json.dumps({
            "departments": {"rd": {"name": "R&D", "reporting_to": None}},
            "created_at": "2026-11-10T00:00:00Z"}))
        (cd / "manifest.json").write_text(json.dumps({
            "project_name": "chat-human", "mode": "build", "auto_mode": False}))
        (cd / "chat" / "general.md").write_text(self.CHANNEL_MD)
        return root

    def test_read_human_transcript(self, seeded):
        cd = seeded / ".company"
        r = self._cli(seeded, str(cd), "chat", "read", "general")
        assert r.returncode == 0, r.stderr
        lines = r.stdout.strip().splitlines()
        assert lines[0] == "#general — 2 message(s)"
        assert lines[1] == "09:30 user: sprint 3 starting"
        assert lines[2] == "09:45 Eng-1: on it"
        for key in ("exit_code", "stderr", '"ok"', '"data"', '"messages"'):
            assert key not in r.stdout

    def test_read_json_is_the_016_envelope(self, seeded):
        cd = seeded / ".company"
        r = self._cli(seeded, str(cd), "chat", "read", "general", "--json")
        assert r.returncode == 0, r.stderr
        env = json.loads(r.stdout)
        assert set(env.keys()) == {"ok", "exit_code", "stderr", "data"}
        data = env["data"]
        assert set(data.keys()) == {"channel", "count", "messages"}
        assert data["channel"] == "general" and data["count"] == 2
        assert data["messages"][0]["role"] == "user"
        assert data["messages"][0]["message"] == "sprint 3 starting"
        assert data["messages"][1]["timestamp"] == "2026-11-11T09:45:00"

    def test_read_last_honored_human_and_json(self, seeded):
        cd = seeded / ".company"
        r = self._cli(seeded, str(cd), "chat", "read", "general", "--last", "1")
        assert r.returncode == 0, r.stderr
        lines = r.stdout.strip().splitlines()
        assert lines[0] == "#general — 1 message(s)"
        assert lines[1] == "09:45 Eng-1: on it"
        assert "sprint 3 starting" not in r.stdout

    def test_read_missing_channel_is_an_error(self, seeded):
        cd = seeded / ".company"
        r = self._cli(seeded, str(cd), "chat", "read", "no-such-channel")
        assert r.returncode == 1
        assert "not found" in r.stderr
        r = self._cli(seeded, str(cd), "chat", "read", "no-such-channel", "--json")
        assert r.returncode == 1
        env = json.loads(r.stdout)
        assert env["ok"] is False and "not found" in env["stderr"]

    def test_send_human_confirmation(self, seeded):
        # 0.18 T3: the one-arg form — author/department ride the flags
        # (the 0.17 positional form moved with the change)
        cd = seeded / ".company"
        r = self._cli(seeded, str(cd), "chat", "send", "general",
                      "third message lands here", "--author", "user",
                      "--dept", "rd")
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == "✓ sent to general"
        content = (cd / "chat" / "general.md").read_text()
        assert "third message lands here" in content
        assert "] user (rd)**" in content

    def test_send_json_envelope(self, seeded):
        cd = seeded / ".company"
        r = self._cli(seeded, str(cd), "chat", "send", "general",
                      "fourth message", "--author", "user",
                      "--dept", "rd", "--json")
        assert r.returncode == 0, r.stderr
        env = json.loads(r.stdout)
        assert set(env.keys()) == {"ok", "exit_code", "stderr", "stdout"}
        assert env["ok"] is True
        assert env["stdout"].startswith("Message sent to #general by user")


class TestGatesHumanFace:
    """T1 (0.17): `hive gates` — per-gate / per-ticket summaries by
    default; --json is the 0.16.0 face (the raw envelope for run/check,
    the structured envelope over the same data for status, which was
    already human). Exit codes unchanged on every path."""

    GATES_JSON = {
        "ticket_id": "RD-0103",
        "gates": {
            "worktree-exists": {"status": "pass", "detail": "/wt",
                                "checked_at": "2026-11-11T10:00:00Z"},
            "has-commits": {"status": "fail",
                            "detail": "0 commits beyond main",
                            "checked_at": "2026-11-11T10:00:01Z"},
            "tests-pass": {"status": "skip",
                           "detail": "no test framework detected",
                           "checked_at": "2026-11-11T10:00:02Z"},
        },
    }

    @staticmethod
    def _cli(cwd: Path, company_dir: str, *argv: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "hive_cli.main", *argv],
            capture_output=True, text=True, timeout=120,
            cwd=str(cwd),
            env={**os.environ,
                 "PYTHONPATH": str(Path(__file__).resolve()
                                   .parent.parent / "lib"),
                 "HIVE_MCP_PORT": "1",
                 "COMPANY_DIR": company_dir})

    @pytest.fixture(scope="class")
    def seeded(self, tmp_path_factory):
        root = tmp_path_factory.mktemp("gates-human")
        cd = root / ".company"
        (cd / "departments" / "rd" / "tickets" / "in-review").mkdir(parents=True)
        (cd / "departments" / "rd" / "components" / "RD-0103").mkdir(parents=True)
        ticket = {"id": "RD-0103", "type": "infrastructure",
                  "title": "Blocked one", "status": "in-review",
                  "priority": "high", "gates_passed": False,
                  "verified_by": "", "comments": []}
        (cd / "departments" / "rd" / "tickets" / "in-review" / "RD-0103.json").write_text(
            json.dumps(ticket, indent=2))
        (cd / "departments" / "rd" / "components" / "RD-0103" / "gates.json").write_text(
            json.dumps(self.GATES_JSON, indent=2))
        (cd / "gates.json").write_text(json.dumps({"require_lint": False}))
        return root

    def test_check_human_per_gate_lines(self, seeded):
        cd = seeded / ".company"
        r = self._cli(seeded, str(cd), "gates", "check", "rd", "RD-0103")
        assert r.returncode == 0, r.stderr
        assert "Gates for RD-0103:" in r.stdout
        assert "\u2713 worktree-exists — /wt" in r.stdout
        assert "has-commits — 0 commits beyond main" in r.stdout
        assert "tests-pass — no test framework detected" in r.stdout
        assert "exit_code" not in r.stdout and '"data"' not in r.stdout

    def test_check_json_is_the_016_face(self, seeded):
        cd = seeded / ".company"
        r = self._cli(seeded, str(cd), "gates", "check", "rd", "RD-0103", "--json")
        assert r.returncode == 0, r.stderr
        obj = json.loads(r.stdout)
        # gates_check returns a hand-built dict — 0.16 printed exactly
        # {"ok", "data"}; that face does not move.
        assert set(obj.keys()) == {"ok", "data"}
        assert obj["ok"] is True
        assert obj["data"]["gates"]["has-commits"]["status"] == "fail"

    def test_check_missing_report_is_an_error(self, seeded):
        cd = seeded / ".company"
        r = self._cli(seeded, str(cd), "gates", "check", "rd", "RD-9999")
        assert r.returncode == 1
        assert "no gates report found" in r.stderr
        r = self._cli(seeded, str(cd), "gates", "check", "rd", "RD-9999", "--json")
        obj = json.loads(r.stdout)
        assert obj.get("ok") is False

    def test_status_human_unchanged_layout(self, seeded):
        cd = seeded / ".company"
        r = self._cli(seeded, str(cd), "gates", "status")
        assert r.returncode == 0, r.stderr
        assert "Gates config (" in r.stdout and str(cd / "gates.json") in r.stdout
        assert "  mode:             (unset = relaxed)" in r.stdout
        assert f"  {'require_lint':<17} False" in r.stdout
        assert "  rd: 1 in-review" in r.stdout
        expected_row = (f"    {'RD-0103':<25} BLOCKED (gates not passed)"
                        "  [FAIL: has-commits]")
        assert expected_row in r.stdout

    def test_status_json_structured_envelope(self, seeded):
        cd = seeded / ".company"
        r = self._cli(seeded, str(cd), "gates", "status", "--json")
        assert r.returncode == 0, r.stderr
        obj = json.loads(r.stdout)
        assert set(obj.keys()) == {"ok", "exit_code", "stderr", "data"}
        assert obj["ok"] is True
        rows = obj["data"]["departments"]
        assert rows[0]["dept"] == "rd" and rows[0]["count"] == 1
        t = rows[0]["tickets"][0]
        assert t["id"] == "RD-0103"
        assert t["state"] == "BLOCKED (gates not passed)"

    def test_run_idle_human_and_json(self, tmp_path):
        cd = tmp_path / ".company"
        (cd / "departments" / "rd" / "tickets" / "in-review").mkdir(parents=True)
        r = self._cli(tmp_path, str(cd), "gates", "run", "rd")
        assert r.returncode == 0, r.stdout + r.stderr
        assert "Nothing to gate" in r.stdout
        assert "no in-review tickets in rd" in r.stdout
        r = self._cli(tmp_path, str(cd), "gates", "run", "rd", "--json")
        obj = json.loads(r.stdout)
        assert set(obj.keys()) == {"ok", "exit_code", "stderr", "stdout"}
        assert obj["ok"] is True


class TestBacklogHumanFace:
    """T1 (0.17): `hive backlog prioritize` — the top-N table by
    default; --json is the 0.16.0 envelope (the ranked ids on stdout,
    byte-for-byte — sprint-fill rides that contract)."""

    @staticmethod
    def _cli(cwd: Path, company_dir: str, *argv: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "hive_cli.main", *argv],
            capture_output=True, text=True, timeout=120,
            cwd=str(cwd),
            env={**os.environ,
                 "PYTHONPATH": str(Path(__file__).resolve()
                                   .parent.parent / "lib"),
                 "HIVE_MCP_PORT": "1",
                 "COMPANY_DIR": company_dir})

    @staticmethod
    def _ticket(tid, pri, title="t"):
        return {"id": tid, "type": "feature", "title": title,
                "description": f"d {tid}", "created_by": "user",
                "filed_by_department": "rd", "source": "manual",
                "priority": pri, "status": "backlog",
                "created_at": f"2026-11-10T00:00:0{int(tid[-1])}Z",
                "updated_at": "2026-11-10T00:00:00Z", "comments": []}

    @pytest.fixture(scope="class")
    def seeded(self, tmp_path_factory):
        root = tmp_path_factory.mktemp("backlog-human")
        cd = root / ".company"
        bl = cd / "departments" / "rd" / "tickets" / "backlog"
        bl.mkdir(parents=True)
        (cd / "departments" / "it" / "tickets" / "backlog").mkdir(parents=True)
        for tid, pri, title in [("RD-0201", "high", "High ticket"),
                                ("RD-0202", "medium", "Medium ticket"),
                                ("RD-0203", "low", "Low ticket")]:
            (bl / f"{tid}.json").write_text(json.dumps(self._ticket(tid, pri, title)))
        return root

    def test_top_n_table_human(self, seeded):
        cd = seeded / ".company"
        r = self._cli(seeded, str(cd), "backlog", "prioritize", "rd", "--limit", "2")
        assert r.returncode == 0, r.stdout + r.stderr
        assert "Top 2 ticket(s) from rd's backlog:" in r.stdout
        # header order per the goals doc: priority, type, title, id
        header = r.stdout.splitlines()[1]
        assert header.split() == ["PRI", "TYPE", "TITLE", "ID"]
        # rank order: high before medium; ids on the matching rows
        rows = r.stdout.splitlines()[2:]
        assert rows[0].split() == ["high", "feature", "High", "ticket", "RD-0201"]
        assert rows[1].split() == ["medium", "feature", "Medium", "ticket", "RD-0202"]
        assert "RD-0203" not in r.stdout

    def test_limit_one(self, seeded):
        cd = seeded / ".company"
        r = self._cli(seeded, str(cd), "backlog", "prioritize", "rd")
        assert r.returncode == 0, r.stdout + r.stderr
        assert "Top 1 ticket(s)" in r.stdout
        assert "RD-0201" in r.stdout and "RD-0202" not in r.stdout

    def test_json_is_the_016_envelope(self, seeded):
        cd = seeded / ".company"
        r = self._cli(seeded, str(cd), "backlog", "prioritize", "rd",
                      "--limit", "2", "--json")
        assert r.returncode == 0, r.stderr
        obj = json.loads(r.stdout)
        # plain-text script output — 0.16's envelope carried it on stdout
        assert set(obj.keys()) == {"ok", "exit_code", "stderr", "stdout"}
        assert obj["ok"] is True
        assert obj["stdout"].splitlines() == ["RD-0201", "RD-0202"]

    def test_empty_backlog_is_named(self, seeded):
        cd = seeded / ".company"
        r = self._cli(seeded, str(cd), "backlog", "prioritize", "it")
        assert r.returncode == 0, r.stdout + r.stderr
        assert "Backlog for it is empty." in r.stdout


class TestAgentHumanFace:
    """T1 (0.17): `hive agent spawn-ceo` / `run-vp` — the default is a
    one-line confirmation (agent id + the log file to tail); --json is
    the 0.16.0 result dict byte-for-byte (agent-spawn's stdout is
    stream-json — that raw face is the machine contract). The error
    face surfaces the harness's real error (it reports on the
    envelope's stdout; its own stderr is spawn plumbing)."""

    @staticmethod
    def _env_clean(tmp_path) -> dict:
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("HIVE_")}
        env.update({
            "HOME": str(tmp_path / "home"),
            "HIVE_CONFIG": str(tmp_path / "home" / "no-config.toml"),
            "HIVE_MCP_PORT": "1",
            "PYTHONPATH": str(Path(__file__).resolve().parent.parent / "lib"),
        })
        (tmp_path / "home").mkdir(exist_ok=True)
        return env

    def test_spawn_ceo_success_confirmation(self, tmp_path, monkeypatch, capsys):
        from hive_cli.commands import agent
        cd = tmp_path / ".company"
        cd.mkdir()
        monkeypatch.setenv("COMPANY_DIR", str(cd))
        monkeypatch.setattr(agent.mcp_client, "call_tool",
                            lambda name, payload: {
                                "ok": True, "exit_code": 0,
                                "stdout": '{"type":"event"}\n', "stderr": ""})
        import argparse
        rc = agent.handle(argparse.Namespace(
            action="spawn-ceo", project_dir=str(tmp_path),
            mode="FRESH", json=False))
        out = capsys.readouterr().out
        assert rc == 0
        assert "ceo spawned (mode=FRESH)" in out
        assert f"log: {cd}/logs/spawns.log" in out
        assert '{"type":"event"}' not in out  # the stream stays out of the human face

    def test_spawn_ceo_json_is_the_016_face(self, tmp_path, monkeypatch, capsys):
        from hive_cli.commands import agent
        cd = tmp_path / ".company"
        cd.mkdir()
        monkeypatch.setenv("COMPANY_DIR", str(cd))
        monkeypatch.setattr(agent.mcp_client, "call_tool",
                            lambda name, payload: {
                                "ok": True, "exit_code": 0,
                                "stdout": '{"type":"event"}\n',
                                "stderr": "venv warning"})
        import argparse
        rc = agent.handle(argparse.Namespace(
            action="spawn-ceo", project_dir=str(tmp_path),
            mode="FRESH", json=True))
        out = capsys.readouterr().out
        assert rc == 0
        obj = json.loads(out)
        assert set(obj) == {"ok", "exit_code", "stdout", "stderr"}
        assert obj["stdout"] == '{"type":"event"}\n'

    def test_run_vp_success_confirmation(self, tmp_path, monkeypatch, capsys):
        from hive_cli.commands import agent
        monkeypatch.delenv("COMPANY_DIR", raising=False)
        monkeypatch.setattr(agent.mcp_client, "call_tool",
                            lambda name, payload: {
                                "ok": True, "exit_code": 0,
                                "stdout": "", "stderr": ""})
        import argparse
        rc = agent.handle(argparse.Namespace(
            action="run-vp", dept="rd", project_dir=str(tmp_path),
            json=False))
        out = capsys.readouterr().out
        assert rc == 0
        assert "VP cycle started for rd (agent vp-rd)" in out

    def test_spawn_ceo_unconfigured_llm_names_the_fix(self, tmp_path):
        # End-to-end: no LLM anywhere (clean HOME, no HIVE_*), so the
        # harness fails fast — the human error face must carry the
        # harness's real error (which names the config file), not the
        # spawn plumbing warning that fills the envelope stderr.
        cd = tmp_path / "proj" / ".company"
        cd.mkdir(parents=True)
        (cd / "manifest.json").write_text(
            json.dumps({"project_name": "probe", "mode": "build"}))
        r = subprocess.run(
            [sys.executable, "-m", "hive_cli.main",
             "agent", "spawn-ceo", str(tmp_path / "proj")],
            capture_output=True, text=True, timeout=180,
            cwd=str(tmp_path / "proj"), env=self._env_clean(tmp_path))
        assert r.returncode == 1, r.stdout + r.stderr
        assert "no LLM provider configured" in r.stderr
        assert "config.toml" in r.stderr


class TestGHHumanFace:
    """T1 (0.17): `hive gh status/pr open/pr merge` — the aligned
    lines and the one-line confirmations by default; --json is the
    0.16.0 output byte-for-byte (the raw dict for status, the shell
    envelope for pr ops). In-process: the MCP result is canned, so no
    git remote is needed."""

    @staticmethod
    def _ns(**kw):
        import argparse
        base = {"action": "status", "json": False, "pr_action": "open",
                "branch": "feature/T-1", "ticket": "T-1", "method": "merge"}
        base.update(kw)
        return argparse.Namespace(**base)

    def test_status_human_aligned_lines(self, tmp_path, monkeypatch, capsys):
        from hive_cli.commands import gh
        monkeypatch.setattr(gh.mcp_client, "call_tool",
                            lambda name, payload: {
                                "installed": True, "authed": True,
                                "remote": "git@github.com:acme/widget.git",
                                "default_branch": "main"})
        rc = gh.handle(self._ns())
        out = capsys.readouterr().out
        assert rc == 0
        assert "gh CLI:" in out and "installed" in out
        assert "authenticated" in out
        assert "git@github.com:acme/widget.git" in out
        assert "main" in out

    def test_status_human_names_the_missing_gh(self, tmp_path, monkeypatch, capsys):
        from hive_cli.commands import gh
        monkeypatch.setattr(gh.mcp_client, "call_tool",
                            lambda name, payload: {
                                "installed": False, "authed": False,
                                "remote": None, "default_branch": None})
        rc = gh.handle(self._ns())
        out = capsys.readouterr().out
        assert rc == 0  # a reported state, not an error
        assert "not installed" in out

    def test_status_json_is_the_raw_dict(self, tmp_path, monkeypatch, capsys):
        from hive_cli.commands import gh
        data = {"installed": True, "authed": False, "remote": None,
                "default_branch": None}
        monkeypatch.setattr(gh.mcp_client, "call_tool",
                            lambda name, payload: data)
        rc = gh.handle(self._ns(json=True))
        out = capsys.readouterr().out
        assert rc == 0
        assert json.loads(out) == data  # byte-for-byte the 0.16 face

    def test_pr_open_human_confirmation(self, tmp_path, monkeypatch, capsys):
        from hive_cli.commands import gh
        env = {"ok": True, "exit_code": 0, "stderr": "",
               "stdout": ("gh-pr-open: opening PR for feature/T-1 -> main (ticket=T-1)\n"
                          "gh-pr-open: opened PR #5 (https://github.com/acme/widget/pull/5)\n"
                          "5")}
        monkeypatch.setattr(gh.mcp_client, "call_tool",
                            lambda name, payload: env)
        rc = gh.handle(self._ns(action="pr"))
        out = capsys.readouterr().out
        assert rc == 0
        assert "opened PR #5" in out
        assert "https://github.com/acme/widget/pull/5" in out

    def test_pr_open_json_is_the_envelope(self, tmp_path, monkeypatch, capsys):
        from hive_cli.commands import gh
        env = {"ok": True, "exit_code": 0, "stderr": "",
               "stdout": "gh-pr-open: opened PR #5 (https://github.com/acme/widget/pull/5)\n5"}
        monkeypatch.setattr(gh.mcp_client, "call_tool",
                            lambda name, payload: env)
        rc = gh.handle(self._ns(action="pr", json=True))
        assert rc == 0
        obj = json.loads(capsys.readouterr().out)
        assert set(obj) == {"ok", "exit_code", "stderr", "stdout"}

    def test_pr_open_failure_is_exit_1_with_the_reason(self, tmp_path,
                                                       monkeypatch, capsys):
        from hive_cli.commands import gh
        monkeypatch.setattr(gh.mcp_client, "call_tool",
                            lambda name, payload: {
                                "ok": False, "exit_code": 1,
                                "stderr": "gh pr create failed: not a git repo",
                                "stdout": ""})
        rc = gh.handle(self._ns(action="pr"))
        err = capsys.readouterr().err
        assert rc == 1
        assert "gh pr create failed" in err


class TestReleaseHumanFace:
    """T1 (0.17): hive release status/list render human by default;
    --json is the 0.16.0 raw dict byte-for-byte (these MCP tools
    return plain dicts, not shell envelopes)."""

    @staticmethod
    def _ns(**kw):
        import argparse
        base = {"action": "status", "version": None, "limit": 20, "json": False}
        base.update(kw)
        return argparse.Namespace(**base)

    def test_status_human(self, monkeypatch, capsys):
        from hive_cli.commands import release
        monkeypatch.setenv("COMPANY_DIR", "/tmp/no-such-company-t1h")
        monkeypatch.setattr(
            release.mcp_client, "call_tool",
            lambda name, payload: {"sprint_id": "sprint-1", "stage": "release-verify",
                                   "release": {"version": "0.17.0",
                                               "publish_status": "pending"}})
        rc = release.handle(self._ns())
        out = capsys.readouterr().out
        assert rc == 0
        assert "sprint=sprint-1" in out
        assert "version: 0.17.0" in out
        assert "publish_status: pending" in out
        assert "stage" in out

    def test_status_json_raw_dict(self, monkeypatch, capsys):
        from hive_cli.commands import release
        monkeypatch.setenv("COMPANY_DIR", "/tmp/no-such-company-t1h")
        payload = {"sprint_id": "sprint-1", "stage": "build-and-test",
                   "release": {"version": "0.16.0"}}
        monkeypatch.setattr(release.mcp_client, "call_tool",
                            lambda name, p: payload)
        rc = release.handle(self._ns(json=True))
        out = capsys.readouterr().out
        assert rc == 0
        assert json.loads(out) == payload  # byte-for-byte face, same keys

    def test_status_no_record_named(self, monkeypatch, capsys):
        from hive_cli.commands import release
        monkeypatch.setenv("COMPANY_DIR", "/tmp/no-such-company-t1h")
        monkeypatch.setattr(release.mcp_client, "call_tool",
                            lambda name, p: {"error": "no release record found"})
        rc = release.handle(self._ns())
        out = capsys.readouterr().out
        assert rc == 0
        assert "No release record found" in out

    def test_list_human_table(self, monkeypatch, capsys):
        from hive_cli.commands import release
        monkeypatch.setenv("COMPANY_DIR", "/tmp/no-such-company-t1h")
        monkeypatch.setattr(
            release.mcp_client, "call_tool",
            lambda name, p: {"releases": [
                {"sprint_id": "sprint-2", "version": "0.16.0",
                 "publish_status": "published",
                 "tarball": "hive-0.16.0.tar.gz",
                 "ended_at": "2026-11-01T00:00:00Z"}]})
        rc = release.handle(self._ns(action="list"))
        out = capsys.readouterr().out
        assert rc == 0
        assert "0.16.0" in out and "published" in out
        assert "sprint-2" in out

    def test_list_empty_named(self, monkeypatch, capsys):
        from hive_cli.commands import release
        monkeypatch.setenv("COMPANY_DIR", "/tmp/no-such-company-t1h")
        monkeypatch.setattr(release.mcp_client, "call_tool",
                            lambda name, p: {"releases": []})
        rc = release.handle(self._ns(action="list"))
        out = capsys.readouterr().out
        assert rc == 0
        assert "No releases" in out

    def test_list_json_raw_dict(self, monkeypatch, capsys):
        from hive_cli.commands import release
        monkeypatch.setenv("COMPANY_DIR", "/tmp/no-such-company-t1h")
        payload = {"releases": []}
        monkeypatch.setattr(release.mcp_client, "call_tool",
                            lambda name, p: payload)
        rc = release.handle(self._ns(action="list", json=True))
        assert json.loads(capsys.readouterr().out) == payload
        assert rc == 0


class TestTicketListCompanyWideCli:
    """T2 (0.17): `hive ticket list` with no department lists the whole
    company — grouped per department (count-0 depts stay visible);
    a named dept that does not exist is an error, not an empty list."""

    @staticmethod
    def _cli(cwd: Path, company_dir: str, *argv: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "hive_cli.main", *argv],
            capture_output=True, text=True, timeout=120,
            cwd=str(cwd),
            env={**os.environ,
                 "PYTHONPATH": str(Path(__file__).resolve()
                                   .parent.parent / "lib"),
                 "HIVE_MCP_PORT": "1",
                 "COMPANY_DIR": company_dir})

    @staticmethod
    def _seed(root: Path):
        cd = root / ".company"
        (cd / "departments" / "rd" / "tickets" / "in-progress").mkdir(parents=True)
        (cd / "departments" / "it" / "tickets" / "backlog").mkdir(parents=True)
        (cd / "departments" / "hr" / "tickets").mkdir(parents=True)
        (cd / "departments" / "rd" / "tickets" / "in-progress"
         / "RD-0101.json").write_text(json.dumps({
            "id": "RD-0101", "type": "feature", "title": "R one",
            "description": "", "status": "in-progress",
            "priority": "high", "assignee": {"name": "Eng-1"}}))
        (cd / "departments" / "it" / "tickets" / "backlog"
         / "IT-0101.json").write_text(json.dumps({
            "id": "IT-0101", "type": "chore", "title": "I one",
            "description": "", "status": "backlog",
            "priority": "medium", "assignee": {}}))
        return cd

    def test_company_wide_human_grouped(self, tmp_path):
        cd = self._seed(tmp_path)
        r = self._cli(tmp_path, str(cd), "ticket", "list")
        assert r.returncode == 0, r.stderr
        assert "rd:" in r.stdout
        assert "it:" in r.stdout
        assert "hr:" in r.stdout
        assert "RD-0101" in r.stdout and "Eng-1" in r.stdout
        assert "0 ticket(s) in hr" in r.stdout
        assert "2 ticket(s) company-wide across 3 department(s)" in r.stdout

    def test_company_wide_json_document(self, tmp_path):
        cd = self._seed(tmp_path)
        r = self._cli(tmp_path, str(cd), "ticket", "list", "--json")
        assert r.returncode == 0, r.stderr
        data = json.loads(r.stdout)
        assert set(data.keys()) == {"ok", "exit_code", "stderr", "data"}
        doc = data["data"]
        assert set(doc.keys()) == {"departments", "total", "mode"}
        assert doc["total"] == 2
        assert set(doc["departments"].keys()) == {"rd", "it", "hr"}
        assert doc["departments"]["hr"]["count"] == 0

    def test_named_missing_dept_is_an_error(self, tmp_path):
        cd = self._seed(tmp_path)
        r = self._cli(tmp_path, str(cd), "ticket", "list", "nope")
        assert r.returncode == 1
        assert "Department 'nope' not found" in r.stderr
        r = self._cli(tmp_path, str(cd), "ticket", "list", "nope", "--json")
        data = json.loads(r.stdout)
        assert data["ok"] is False
        assert "not found" in data["stderr"]


class TestStatusEmptyStates:
    """T3 (0.17): `hive status` names its empty states.

    A fresh company (org-chart scaffolded, no runtime state) reads as
    healthy-and-waiting: the board at zero, the idle/missing sprint,
    the missing daemons, and the LLM hint when the LLM is unconfigured.
    A department that does not exist is an error that names it."""

    @staticmethod
    def _fresh(tmp_path):
        cd = tmp_path / ".company"
        (cd / "departments" / "it" / "tickets").mkdir(parents=True)
        (cd / "org-chart.json").write_text(json.dumps(
            {"departments": {"it": {"name": "IT"}, "rd": {"name": "R&D"}}}))
        (cd / "manifest.json").write_text(json.dumps(
            {"project_name": "fresh", "mode": "build"}))
        return tmp_path

    @staticmethod
    def _args(tmp_path, dept=None, as_json=False):
        import argparse
        return argparse.Namespace(project_dir=str(tmp_path), dept=dept,
                                  json=as_json)

    def test_fresh_company_names_every_state(self, tmp_path, monkeypatch, capsys):
        from hive_cli.commands import company
        # The MCP reports 'no departments dir' (true: only it/ has a
        # tickets dir... actually the dir exists) — use the empty-data
        # success path plus the no-sprint, no-daemons, unconfigured-LLM
        # states.
        monkeypatch.setattr(company.mcp_client, "call_tool",
                            lambda name, payload: {"ok": True, "data": {}})
        monkeypatch.setattr(company, "_llm_hint",
                            lambda cd: "LLM not configured — set the [llm] section")
        rc = company.handle_status(self._args(self._fresh(tmp_path)))
        out = capsys.readouterr().out
        assert rc == 0
        # Existing-but-empty departments are listed at zero
        assert "it" in out and "rd" in out
        assert "bl=0 sp=0 ip=0 ir=0 done=0" in out
        assert "No tickets anywhere yet" in out
        # The sprint and daemon empty states name themselves
        assert "Sprint: none" in out
        assert "hive start" in out
        assert "Daemons: none running" in out
        # The unconfigured LLM hint is the canonical one
        assert "LLM not configured" in out

    def test_missing_dept_is_a_named_error(self, tmp_path, monkeypatch, capsys):
        from hive_cli.commands import company
        monkeypatch.setattr(company.mcp_client, "call_tool",
                            lambda name, payload: {"ok": True, "data": {}})
        rc = company.handle_status(self._args(self._fresh(tmp_path),
                                              dept="marketing"))
        err = capsys.readouterr().err
        assert rc == 1
        assert "Department 'marketing' not found" in err
        assert "it" in err and "rd" in err

    def test_missing_dept_json_is_an_error_envelope(self, tmp_path, monkeypatch, capsys):
        from hive_cli.commands import company
        monkeypatch.setattr(company.mcp_client, "call_tool",
                            lambda name, payload: {"ok": True, "data": {}})
        rc = company.handle_status(self._args(self._fresh(tmp_path),
                                              dept="marketing", as_json=True))
        out = json.loads(capsys.readouterr().out)
        assert rc == 1
        assert out["ok"] is False
        assert "Department 'marketing' not found" in out["stderr"]

    def test_no_orgchart_is_the_init_hint(self, tmp_path, monkeypatch, capsys):
        from hive_cli.commands import company
        (tmp_path / ".company").mkdir()
        monkeypatch.setattr(company.mcp_client, "call_tool",
                            lambda name, payload: {
                                "ok": False,
                                "stderr": f"no departments dir at {tmp_path}"})
        rc = company.handle_status(self._args(tmp_path))
        out = capsys.readouterr().out
        assert rc == 0, out
        assert "hive init" in out


class TestStartLlmPreflight:
    """T3 (0.17): `hive start` warns on stderr when the LLM is
    unconfigured — the daemons still start (exit 0); the runner halts
    with a clear reason at the first LLM call."""

    @staticmethod
    def _fake_start(tmp_path):
        import types
        proc = types.SimpleNamespace(wait=lambda timeout=120: 0)
        (tmp_path / ".company" / "logs").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".company" / "processes.json").write_text(json.dumps(
            {"processes": [{"pid": os.getpid(), "role": "hive-mcp"}],
             "started_at": "2026-11-11T00:00:00Z"}))
        return proc

    def test_unconfigured_llm_warns_but_starts(self, tmp_path, monkeypatch, capsys):
        from hive_cli.commands import company
        (tmp_path / ".company").mkdir(exist_ok=True)
        monkeypatch.setattr(company.subprocess, "Popen",
                            lambda *a, **k: self._fake_start(tmp_path))
        monkeypatch.setattr(company, "_llm_hint",
                            lambda cd: ("edit the [llm] section of "
                                        "/cfg/config.toml (hive config edit)"))
        rc = company.handle_start(type("A", (), {"project_dir": str(tmp_path)})())
        captured = capsys.readouterr()
        assert rc == 0
        assert "Company started" in captured.out
        assert "LLM not configured" in captured.err
        assert "agents will fail to spawn" in captured.err
        assert "config.toml" in captured.err

    def test_configured_llm_is_silent(self, tmp_path, monkeypatch, capsys):
        from hive_cli.commands import company
        (tmp_path / ".company").mkdir(exist_ok=True)
        monkeypatch.setattr(company.subprocess, "Popen",
                            lambda *a, **k: self._fake_start(tmp_path))
        monkeypatch.setattr(company, "_llm_hint", lambda cd: None)
        rc = company.handle_start(type("A", (), {"project_dir": str(tmp_path)})())
        captured = capsys.readouterr()
        assert rc == 0
        assert "LLM not configured" not in captured.err


class TestChatChannels:
    """T3 (0.17): `hive chat channels` — the board's channels with
    message counts and last-message times; an empty board names itself
    instead of printing nothing."""

    @staticmethod
    def _cli(cwd: Path, company_dir: str, *argv: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "hive_cli.main", *argv],
            capture_output=True, text=True, timeout=120,
            cwd=str(cwd),
            env={**os.environ,
                 "PYTHONPATH": str(Path(__file__).resolve()
                                   .parent.parent / "lib"),
                 "HIVE_MCP_PORT": "1",
                 "COMPANY_DIR": company_dir})

    @pytest.fixture(scope="class")
    def seeded(self, tmp_path_factory):
        root = tmp_path_factory.mktemp("chat-channels")
        cd = root / ".company"
        (cd / "chat").mkdir(parents=True)
        (cd / "chat" / "general.md").write_text(
            "# Channel: general\n\n---\n"
            "**[2026-11-11T09:30:00Z] user (general)** hi\n\n---\n"
            "**[2026-11-11T09:45:00Z] Eng-1 (rd)** hello\n")
        (cd / "chat" / "engineering.md").write_text(
            "# Channel: engineering\n\n---\n"
            "**[2026-11-11T10:00:00Z] Infra (it)** build green\n")
        return root

    def test_channels_human_table(self, seeded):
        cd = seeded / ".company"
        r = self._cli(seeded, str(cd), "chat", "channels")
        assert r.returncode == 0, r.stderr
        assert "general" in r.stdout
        assert "engineering" in r.stdout
        # counts and last-message times
        assert "2" in r.stdout
        assert "2026-11-11T09:45:00Z" in r.stdout
        assert "2026-11-11T10:00:00Z" in r.stdout
        assert '"ok"' not in r.stdout

    def test_channels_json(self, seeded):
        cd = seeded / ".company"
        r = self._cli(seeded, str(cd), "chat", "channels", "--json")
        assert r.returncode == 0, r.stderr
        d = json.loads(r.stdout)
        data = d["data"]
        assert set(data.keys()) == {"channels", "total_messages"}
        by = {c["channel"]: c for c in data["channels"]}
        assert by["general"]["messages"] == 2
        assert by["general"]["last_at"] == "2026-11-11T09:45:00Z"
        assert by["engineering"]["messages"] == 1
        assert data["total_messages"] == 3

    def test_no_chat_dir_names_the_empty_board(self, tmp_path):
        (tmp_path / ".company").mkdir()
        r = self._cli(tmp_path, str(tmp_path / ".company"), "chat", "channels")
        assert r.returncode == 0, r.stderr
        assert "No channels yet" in r.stdout
        d = json.loads(self._cli(tmp_path, str(tmp_path / ".company"),
                                 "chat", "channels", "--json").stdout)
        assert d["data"]["channels"] == []
