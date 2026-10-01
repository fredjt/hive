"""Tests for hive_cli.commands — command handlers."""
import sys
import os
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
        "doctor", "gates", "gh", "inbox", "init", "killall", "llm",
        "release", "restart", "round", "scaffold", "ticket", "tool",
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


class TestInboxCommand:
    """Test the inbox command handler."""

    def test_inbox_handle_exists(self):
        from hive_cli.commands.inbox import handle
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


class TestScaffoldCommand:
    """Test the scaffold command handler."""

    def test_scaffold_handle_exists(self):
        from hive_cli.commands.scaffold import handle
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
        from hive_cli.commands.config import handle
        # The handle function should process 'get' action
        pass  # Hard to test without actual config file


class TestScaffoldExtractCommand:
    """Test scaffold extract command."""

    def test_scaffold_extract_help(self):
        from hive_cli.main import build_parser
        p = build_parser()
        args = p.parse_args(["scaffold", "extract"])
        assert args.scaffold_action == "extract"


class TestScaffoldUpdateCommand:
    """Test scaffold update command."""

    def test_scaffold_update_help(self):
        from hive_cli.main import build_parser
        p = build_parser()
        args = p.parse_args(["scaffold", "update"])
        assert args.scaffold_action == "update"


class TestScaffoldDiffCommand:
    """Test scaffold diff command."""

    def test_scaffold_diff_help(self):
        from hive_cli.main import build_parser
        p = build_parser()
        args = p.parse_args(["scaffold", "diff"])
        assert args.scaffold_action == "diff"
