"""Tests for hive_mcp.tools — MCP tool registration and behavior."""
import sys
import json
import os
from pathlib import Path
# Ensure lib/ is on the path when running from within hive/ directory
_hive_lib = str(Path(__file__).resolve().parent.parent / "lib")
if _hive_lib not in sys.path:
    sys.path.insert(0, _hive_lib)



import pytest


import pytest
try:
    from mcp.server.fastmcp import FastMCP
    _HAS_FASTMCP = True
except ImportError:
    _HAS_FASTMCP = False

@pytest.mark.skipif(not _HAS_FASTMCP, reason="mcp.server.fastmcp not available")
class TestToolModulesExist:
    """Verify all expected tool modules are importable."""

    def test_all_tool_modules_import(self, install_dir):
        """All modules listed in TOOL_MODULES should be importable."""
        sys_path = str(install_dir / "lib")
        old_path = os.environ.get("PYTHONPATH", "")
        if sys_path not in old_path:
            os.environ["PYTHONPATH"] = sys_path + (":" + old_path if old_path else "")

        try:
            from hive_mcp.server import TOOL_MODULES
            for mod_name in TOOL_MODULES:
                # Should not raise ImportError
                __import__(f"hive_mcp.tools.{mod_name}")
        finally:
            if old_path:
                os.environ["PYTHONPATH"] = old_path
            else:
                os.environ.pop("PYTHONPATH", None)


@pytest.mark.skipif(not _HAS_FASTMCP, reason='mcp.server.fastmcp not available')
class TestToolRegistration:
    """Test that tools register correctly with the FastMCP server."""

    def test_build_server_includes_all_tools(self, install_dir):
        """build_server() should register all expected tools."""
        sys_path = str(install_dir / "lib")
        old_path = os.environ.get("PYTHONPATH", "")
        if sys_path not in old_path:
            os.environ["PYTHONPATH"] = sys_path + (":" + old_path if old_path else "")

        try:
            from hive_mcp.server import build_server, TOOL_MODULES
            server = build_server()
            tools = list(server._tool_manager._tools.keys())

            # Should have a substantial number of tools
            assert len(tools) >= 40, f"Expected >= 40 tools, got {len(tools)}: {tools}"

            # Check for some key tool names
            expected_tools = [
                "ticket_create", "ticket_get", "ticket_list", "ticket_move",
                "ticket_approve", "ticket_bounce", "ticket_reject",
                "chat_send", "chat_read",
                "agent_spawn_ceo", "agent_run_vp", "agent_verify",
                "gates_run", "gates_check",
                "status_get",
                "round_status", "round_enqueue", "round_advance",
            ]
            for tool in expected_tools:
                assert tool in tools, f"Expected tool '{tool}' not found. Available: {tools}"
        finally:
            if old_path:
                os.environ["PYTHONPATH"] = old_path
            else:
                os.environ.pop("PYTHONPATH", None)

    def test_ticket_approve_exists(self, install_dir):
        """ticket_approve should be a registered tool."""
        sys_path = str(install_dir / "lib")
        old_path = os.environ.get("PYTHONPATH", "")
        if sys_path not in old_path:
            os.environ["PYTHONPATH"] = sys_path + (":" + old_path if old_path else "")

        try:
            from hive_mcp.server import build_server
            server = build_server()
            tools = list(server._tool_manager._tools.keys())
            assert "ticket_approve" in tools
        finally:
            if old_path:
                os.environ["PYTHONPATH"] = old_path
            else:
                os.environ.pop("PYTHONPATH", None)

    def test_ticket_bounce_exists(self, install_dir):
        """ticket_bounce should be a registered tool."""
        sys_path = str(install_dir / "lib")
        old_path = os.environ.get("PYTHONPATH", "")
        if sys_path not in old_path:
            os.environ["PYTHONPATH"] = sys_path + (":" + old_path if old_path else "")

        try:
            from hive_mcp.server import build_server
            server = build_server()
            tools = list(server._tool_manager._tools.keys())
            assert "ticket_bounce" in tools
        finally:
            if old_path:
                os.environ["PYTHONPATH"] = old_path
            else:
                os.environ.pop("PYTHONPATH", None)


@pytest.mark.skipif(not _HAS_FASTMCP, reason='mcp.server.fastmcp not available')
class TestTicketsToolModule:
    """Test the tickets tool module specifically."""

    def test_ticket_create_has_docstring(self, install_dir):
        sys_path = str(install_dir / "lib")
        old_path = os.environ.get("PYTHONPATH", "")
        if sys_path not in old_path:
            os.environ["PYTHONPATH"] = sys_path + (":" + old_path if old_path else "")

        try:
            from hive_mcp.tools import tickets
            assert tickets.ticket_create.__doc__ is not None
            assert len(tickets.ticket_create.__doc__) > 50
        finally:
            if old_path:
                os.environ["PYTHONPATH"] = old_path
            else:
                os.environ.pop("PYTHONPATH", None)

    def test_ticket_approve_has_docstring(self, install_dir):
        sys_path = str(install_dir / "lib")
        old_path = os.environ.get("PYTHONPATH", "")
        if sys_path not in old_path:
            os.environ["PYTHONPATH"] = sys_path + (":" + old_path if old_path else "")

        try:
            from hive_mcp.tools import tickets
            assert tickets.ticket_approve.__doc__ is not None
            assert "atomic" in tickets.ticket_approve.__doc__.lower()
        finally:
            if old_path:
                os.environ["PYTHONPATH"] = old_path
            else:
                os.environ.pop("PYTHONPATH", None)


@pytest.mark.skipif(not _HAS_FASTMCP, reason='mcp.server.fastmcp not available')
class TestServerBuild:
    """Test server build process."""

    def test_server_has_instructions(self, install_dir):
        sys_path = str(install_dir / "lib")
        old_path = os.environ.get("PYTHONPATH", "")
        if sys_path not in old_path:
            os.environ["PYTHONPATH"] = sys_path + (":" + old_path if old_path else "")

        try:
            from hive_mcp.server import build_server
            server = build_server()
            assert server._state is not None  # FastMCP has state
        finally:
            if old_path:
                os.environ["PYTHONPATH"] = old_path
            else:
                os.environ.pop("PYTHONPATH", None)


@pytest.mark.skipif(not _HAS_FASTMCP, reason='mcp.server.fastmcp not available')
class TestAgentToolModule:
    """Test the agents tool module."""

    def test_agent_spawn_ceo_exists(self, install_dir):
        sys_path = str(install_dir / "lib")
        old_path = os.environ.get("PYTHONPATH", "")
        if sys_path not in old_path:
            os.environ["PYTHONPATH"] = sys_path + (":" + old_path if old_path else "")

        try:
            from hive_mcp.server import build_server
            server = build_server()
            tools = list(server._tool_manager._tools.keys())
            assert "agent_spawn_ceo" in tools
        finally:
            if old_path:
                os.environ["PYTHONPATH"] = old_path
            else:
                os.environ.pop("PYTHONPATH", None)


@pytest.mark.skipif(not _HAS_FASTMCP, reason='mcp.server.fastmcp not available')
class TestRoundToolModule:
    """Test the rounds tool module."""

    def test_round_tools_exist(self, install_dir):
        sys_path = str(install_dir / "lib")
        old_path = os.environ.get("PYTHONPATH", "")
        if sys_path not in old_path:
            os.environ["PYTHONPATH"] = sys_path + (":" + old_path if old_path else "")

        try:
            from hive_mcp.server import build_server
            server = build_server()
            tools = list(server._tool_manager._tools.keys())
            # Check for round-related tools
            round_tools = [t for t in tools if "round" in t.lower()]
            assert len(round_tools) >= 2, f"Expected at least 2 round tools, got: {round_tools}"
        finally:
            if old_path:
                os.environ["PYTHONPATH"] = old_path
            else:
                os.environ.pop("PYTHONPATH", None)


@pytest.mark.skipif(not _HAS_FASTMCP, reason='mcp.server.fastmcp not available')
class TestSprintToolModule:
    """Test the sprint tool module."""

    def test_sprint_tools_exist(self, install_dir):
        sys_path = str(install_dir / "lib")
        old_path = os.environ.get("PYTHONPATH", "")
        if sys_path not in old_path:
            os.environ["PYTHONPATH"] = sys_path + (":" + old_path if old_path else "")

        try:
            from hive_mcp.server import build_server
            server = build_server()
            tools = list(server._tool_manager._tools.keys())
            sprint_tools = [t for t in tools if "sprint" in t.lower()]
            assert len(sprint_tools) >= 2, f"Expected at least 2 sprint tools, got: {sprint_tools}"
        finally:
            if old_path:
                os.environ["PYTHONPATH"] = old_path
            else:
                os.environ.pop("PYTHONPATH", None)


@pytest.mark.skipif(not _HAS_FASTMCP, reason='mcp.server.fastmcp not available')
class TestVerificationToolModule:
    """Test the verification tool module."""

    def test_gates_run_exists(self, install_dir):
        sys_path = str(install_dir / "lib")
        old_path = os.environ.get("PYTHONPATH", "")
        if sys_path not in old_path:
            os.environ["PYTHONPATH"] = sys_path + (":" + old_path if old_path else "")

        try:
            from hive_mcp.server import build_server
            server = build_server()
            tools = list(server._tool_manager._tools.keys())
            assert "gates_run" in tools
        finally:
            if old_path:
                os.environ["PYTHONPATH"] = old_path
            else:
                os.environ.pop("PYTHONPATH", None)


@pytest.mark.skipif(not _HAS_FASTMCP, reason='mcp.server.fastmcp not available')
class TestCompanyToolModule:
    """Test the company tool module."""

    def test_company_start_exists(self, install_dir):
        sys_path = str(install_dir / "lib")
        old_path = os.environ.get("PYTHONPATH", "")
        if sys_path not in old_path:
            os.environ["PYTHONPATH"] = sys_path + (":" + old_path if old_path else "")

        try:
            from hive_mcp.server import build_server
            server = build_server()
            tools = list(server._tool_manager._tools.keys())
            assert "company_start" in tools
        finally:
            if old_path:
                os.environ["PYTHONPATH"] = old_path
            else:
                os.environ.pop("PYTHONPATH", None)


@pytest.mark.skipif(not _HAS_FASTMCP, reason='mcp.server.fastmcp not available')
class TestInitToolModule:
    """Test the init tool module."""

    def test_init_workspace_exists(self, install_dir):
        sys_path = str(install_dir / "lib")
        old_path = os.environ.get("PYTHONPATH", "")
        if sys_path not in old_path:
            os.environ["PYTHONPATH"] = sys_path + (":" + old_path if old_path else "")

        try:
            from hive_mcp.server import build_server
            server = build_server()
            tools = list(server._tool_manager._tools.keys())
            assert "init_workspace" in tools
        finally:
            if old_path:
                os.environ["PYTHONPATH"] = old_path
            else:
                os.environ.pop("PYTHONPATH", None)


@pytest.mark.skipif(not _HAS_FASTMCP, reason='mcp.server.fastmcp not available')
class TestChatToolModule:
    """Test the chat tool module."""

    def test_chat_send_exists(self, install_dir):
        sys_path = str(install_dir / "lib")
        old_path = os.environ.get("PYTHONPATH", "")
        if sys_path not in old_path:
            os.environ["PYTHONPATH"] = sys_path + (":" + old_path if old_path else "")

        try:
            from hive_mcp.server import build_server
            server = build_server()
            tools = list(server._tool_manager._tools.keys())
            assert "chat_send" in tools
        finally:
            if old_path:
                os.environ["PYTHONPATH"] = old_path
            else:
                os.environ.pop("PYTHONPATH", None)


@pytest.mark.skipif(not _HAS_FASTMCP, reason='mcp.server.fastmcp not available')
class TestStatusToolModule:
    """Test the status tool module."""

    def test_status_get_exists(self, install_dir):
        sys_path = str(install_dir / "lib")
        old_path = os.environ.get("PYTHONPATH", "")
        if sys_path not in old_path:
            os.environ["PYTHONPATH"] = sys_path + (":" + old_path if old_path else "")

        try:
            from hive_mcp.server import build_server
            server = build_server()
            tools = list(server._tool_manager._tools.keys())
            assert "status_get" in tools
        finally:
            if old_path:
                os.environ["PYTHONPATH"] = old_path
            else:
                os.environ.pop("PYTHONPATH", None)


@pytest.mark.skipif(not _HAS_FASTMCP, reason='mcp.server.fastmcp not available')
class TestWorktreesToolModule:
    """Test the worktrees tool module."""

    def test_worktree_tools_exist(self, install_dir):
        sys_path = str(install_dir / "lib")
        old_path = os.environ.get("PYTHONPATH", "")
        if sys_path not in old_path:
            os.environ["PYTHONPATH"] = sys_path + (":" + old_path if old_path else "")

        try:
            from hive_mcp.server import build_server
            server = build_server()
            tools = list(server._tool_manager._tools.keys())
            worktree_tools = [t for t in tools if "worktree" in t.lower()]
            assert len(worktree_tools) >= 2, f"Expected at least 2 worktree tools, got: {worktree_tools}"
        finally:
            if old_path:
                os.environ["PYTHONPATH"] = old_path
            else:
                os.environ.pop("PYTHONPATH", None)


@pytest.mark.skipif(not _HAS_FASTMCP, reason='mcp.server.fastmcp not available')
class TestInboxToolModule:
    """Test the inbox tool module."""

    def test_inbox_tools_exist(self, install_dir):
        sys_path = str(install_dir / "lib")
        old_path = os.environ.get("PYTHONPATH", "")
        if sys_path not in old_path:
            os.environ["PYTHONPATH"] = sys_path + (":" + old_path if old_path else "")

        try:
            from hive_mcp.server import build_server
            server = build_server()
            tools = list(server._tool_manager._tools.keys())
            inbox_tools = [t for t in tools if "inbox" in t.lower()]
            assert len(inbox_tools) >= 1, f"Expected at least 1 inbox tool, got: {inbox_tools}"
        finally:
            if old_path:
                os.environ["PYTHONPATH"] = old_path
            else:
                os.environ.pop("PYTHONPATH", None)


@pytest.mark.skipif(not _HAS_FASTMCP, reason='mcp.server.fastmcp not available')
class TestReportsToolModule:
    """Test the reports tool module."""

    def test_reports_tools_exist(self, install_dir):
        sys_path = str(install_dir / "lib")
        old_path = os.environ.get("PYTHONPATH", "")
        if sys_path not in old_path:
            os.environ["PYTHONPATH"] = sys_path + (":" + old_path if old_path else "")

        try:
            from hive_mcp.server import build_server
            server = build_server()
            tools = list(server._tool_manager._tools.keys())
            report_tools = [t for t in tools if "report" in t.lower()]
            assert len(report_tools) >= 1, f"Expected at least 1 report tool, got: {report_tools}"
        finally:
            if old_path:
                os.environ["PYTHONPATH"] = old_path
            else:
                os.environ.pop("PYTHONPATH", None)


@pytest.mark.skipif(not _HAS_FASTMCP, reason='mcp.server.fastmcp not available')
class TestLLMTinyToolModule:
    """Test the llm_tiny tool module."""

    def test_llm_tiny_tools_exist(self, install_dir):
        sys_path = str(install_dir / "lib")
        old_path = os.environ.get("PYTHONPATH", "")
        if sys_path not in old_path:
            os.environ["PYTHONPATH"] = sys_path + (":" + old_path if old_path else "")

        try:
            from hive_mcp.server import build_server
            server = build_server()
            tools = list(server._tool_manager._tools.keys())
            llm_tools = [t for t in tools if "llm" in t.lower()]
            assert len(llm_tools) >= 1, f"Expected at least 1 llm tool, got: {llm_tools}"
        finally:
            if old_path:
                os.environ["PYTHONPATH"] = old_path
            else:
                os.environ.pop("PYTHONPATH", None)


@pytest.mark.skipif(not _HAS_FASTMCP, reason='mcp.server.fastmcp not available')
class TestReleaseToolModule:
    """Test the release tool module."""

    def test_release_tools_exist(self, install_dir):
        sys_path = str(install_dir / "lib")
        old_path = os.environ.get("PYTHONPATH", "")
        if sys_path not in old_path:
            os.environ["PYTHONPATH"] = sys_path + (":" + old_path if old_path else "")

        try:
            from hive_mcp.server import build_server
            server = build_server()
            tools = list(server._tool_manager._tools.keys())
            release_tools = [t for t in tools if "release" in t.lower()]
            assert len(release_tools) >= 2, f"Expected at least 2 release tools, got: {release_tools}"
        finally:
            if old_path:
                os.environ["PYTHONPATH"] = old_path
            else:
                os.environ.pop("PYTHONPATH", None)


@pytest.mark.skipif(not _HAS_FASTMCP, reason='mcp.server.fastmcp not available')
class TestGithubToolModule:
    """Test the github tool module."""

    def test_github_tools_exist(self, install_dir):
        sys_path = str(install_dir / "lib")
        old_path = os.environ.get("PYTHONPATH", "")
        if sys_path not in old_path:
            os.environ["PYTHONPATH"] = sys_path + (":" + old_path if old_path else "")

        try:
            from hive_mcp.server import build_server
            server = build_server()
            tools = list(server._tool_manager._tools.keys())
            github_tools = [t for t in tools if "gh_" in t.lower() or "github" in t.lower()]
            assert len(github_tools) >= 1, f"Expected at least 1 github tool, got: {github_tools}"
        finally:
            if old_path:
                os.environ["PYTHONPATH"] = old_path
            else:
                os.environ.pop("PYTHONPATH", None)
