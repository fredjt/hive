"""Tests for hive_mcp.tools — MCP tool registration and behavior."""
import socket
import subprocess
import sys
import re
import json
import os
import tempfile
import time
from pathlib import Path
# Ensure lib/ is on the path when running from within hive/ directory
_hive_lib = str(Path(__file__).resolve().parent.parent / "lib")
if _hive_lib not in sys.path:
    sys.path.insert(0, _hive_lib)

# tests/ is a package — make the shared conftest helpers importable by
# name (the test_state_layer pattern: the shared normalize_clock helper
# lives in conftest, R1/0.11 xdist worker contract).
_hive_tests = str(Path(__file__).resolve().parent)
if _hive_tests not in sys.path:
    sys.path.insert(0, _hive_tests)
from conftest import normalize_clock  # noqa: E402



import pytest


import pytest
try:
    # mcp >= 2.0 renamed FastMCP to MCPServer; mcp 1.x has FastMCP.
    try:
        from mcp.server.mcpserver import MCPServer  # noqa: F401
    except ImportError:
        from mcp.server.fastmcp import FastMCP  # noqa: F401
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
            from hive_mcp.server import build_server
            server = build_server()
            tools = list(server._tool_manager._tools.keys())

            # Should have a substantial number of tools
            assert len(tools) >= 40, f"Expected >= 40 tools, got {len(tools)}: {tools}"

            # Check for some key tool names
            expected_tools = [
                "ticket_create", "ticket_get", "ticket_list", "ticket_move",
                "ticket_approve", "ticket_bounce", "ticket_reject",
                "chat_send", "chat_read",
                "agent_spawn_ceo", "agent_run_vp",
                "gates_run", "gates_check",
                "status_get",
            ]
            for tool in expected_tools:
                assert tool in tools, f"Expected tool '{tool}' not found. Available: {tools}"

            # Intentionally REMOVED tools must stay removed:
            #   - agent_verify: verification ICs are spawned by
            #     sprint-lifecycle during the verify stage (see
            #     lib/hive_mcp/tools/agents.py).
            #   - company_start / company_stop: agents kept invoking them
            #     mid-cycle (see lib/hive_cli/commands/company.py and
            #     lib/dashboard/server.js).
            #   - gh_pr_reject (C.9): gh-pr-reject.sh is a deprecation shim
            #     that unconditionally exits 4; the NO-GO PR path no longer
            #     exists (bounce keeps PRs open, ticket rejection closes the
            #     PR automatically via gh-pr-close.sh).
            for removed in ("agent_verify", "company_start", "company_stop",
                            "gh_pr_reject"):
                assert removed not in tools, (
                    f"tool '{removed}' was intentionally removed but is registered again"
                )
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
        """ticket_create must register with a substantial description.

        Tool functions are closures inside register(server), so inspect the
        registered server rather than module attributes.
        """
        sys_path = str(install_dir / "lib")
        old_path = os.environ.get("PYTHONPATH", "")
        if sys_path not in old_path:
            os.environ["PYTHONPATH"] = sys_path + (":" + old_path if old_path else "")

        try:
            from hive_mcp.server import build_server
            server = build_server()
            tool = server._tool_manager._tools["ticket_create"]
            assert tool.description
            assert len(tool.description) > 50
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
            from hive_mcp.server import build_server
            server = build_server()
            tool = server._tool_manager._tools["ticket_approve"]
            assert tool.description
            assert "atomic" in tool.description.lower()
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
            # Name is set at construction; instructions live on the server
            # (mcp 2.x) or its internal MCP server (mcp 1.x).
            assert server.name == "hive"
            instructions = getattr(server, "instructions", None)
            if instructions is None:
                internal = getattr(server, "_mcp_server", None)
                instructions = getattr(internal, "instructions", None) if internal else None
            assert instructions, "server has no instructions"
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
            # G3: the round queue tools are retired; none may remain.
            round_tools = [t for t in tools if "round" in t.lower()]
            assert not round_tools, f"retired round tools still present: {round_tools}"
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

    def test_gates_run_ticket_id_optional(self, install_dir):
        """B.9: the verification-queue 'Run gates' button scopes a run to
        ONE ticket; the tool must accept an optional ticket_id."""
        import inspect
        sys_path = str(install_dir / "lib")
        old_path = os.environ.get("PYTHONPATH", "")
        if sys_path not in old_path:
            os.environ["PYTHONPATH"] = sys_path + (":" + old_path if old_path else "")
        try:
            from hive_mcp.server import build_server
            server = build_server()
            sig = inspect.signature(server._tool_manager._tools["gates_run"].fn)
            assert "ticket_id" in sig.parameters
            assert sig.parameters["ticket_id"].default is not inspect.Parameter.empty
        finally:
            if old_path:
                os.environ["PYTHONPATH"] = old_path
            else:
                os.environ.pop("PYTHONPATH", None)

    def test_gates_run_project_root_optional(self, install_dir):
        """C.10: project_root is derivable (parent of the company dir, or
        CWD) so clients no longer have to guess the path."""
        import inspect
        sys_path = str(install_dir / "lib")
        old_path = os.environ.get("PYTHONPATH", "")
        if sys_path not in old_path:
            os.environ["PYTHONPATH"] = sys_path + (":" + old_path if old_path else "")
        try:
            from hive_mcp.server import build_server
            server = build_server()
            sig = inspect.signature(server._tool_manager._tools["gates_run"].fn)
            assert "project_root" in sig.parameters
            assert sig.parameters["project_root"].default is not inspect.Parameter.empty
        finally:
            if old_path:
                os.environ["PYTHONPATH"] = old_path
            else:
                os.environ.pop("PYTHONPATH", None)


@pytest.mark.skipif(not _HAS_FASTMCP, reason='mcp.server.fastmcp not available')
class TestCompanyToolModule:
    """Test the company tool module."""

    def test_company_start_stop_removed(self, install_dir):
        """company_start/company_stop are intentionally removed: spawned
        agents kept invoking them mid-cycle. company_status remains.
        """
        sys_path = str(install_dir / "lib")
        old_path = os.environ.get("PYTHONPATH", "")
        if sys_path not in old_path:
            os.environ["PYTHONPATH"] = sys_path + (":" + old_path if old_path else "")

        try:
            from hive_mcp.server import build_server
            server = build_server()
            tools = list(server._tool_manager._tools.keys())
            assert "company_start" not in tools
            assert "company_stop" not in tools
            assert "company_status" in tools
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
class TestLLMUsageToolModule:
    """Test the llm_usage tool module (formerly llm_tiny — B2 rename; the
    tool names themselves never carried the 'tiny' naming)."""

    def test_llm_usage_tools_exist(self, install_dir):
        sys_path = str(install_dir / "lib")
        old_path = os.environ.get("PYTHONPATH", "")
        if sys_path not in old_path:
            os.environ["PYTHONPATH"] = sys_path + (":" + old_path if old_path else "")

        try:
            from hive_mcp.server import build_server
            server = build_server()
            tools = list(server._tool_manager._tools.keys())
            # The local-classification tools register under their
            # functional names, not an llm_* prefix.
            for tool in ("classify_ticket", "triage_test_failure",
                         "narrate_status", "summarize_thread"):
                assert tool in tools, f"Expected llm_usage tool '{tool}' not found"
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


@pytest.mark.skipif(not _HAS_FASTMCP, reason='mcp server not available')
class TestTicketRouteExpeditedTool:
    """ticket_route_expedited must actually create the expedited ticket.

    Regression: the MCP wrapper passed (dept, ticket_id, priority) but
    ticket-route-expedited.sh expects
    (type, priority, title, description, created_by, target_department),
    so every call died at the script's usage check with rc=1.
    Separately, the script's SKILLS_DIR default pointed at
    $HIVE_ROOT/lib/agents (agent prompt files), so the internal
    ticket-create.sh invocation resolved to a nonexistent path.
    """

    def test_tool_creates_expedited_ticket(self, install_dir, tmp_company_dir):
        sys_path = str(install_dir / "lib")
        old_path = os.environ.get("PYTHONPATH", "")
        if sys_path not in old_path:
            os.environ["PYTHONPATH"] = sys_path + (":" + old_path if old_path else "")

        try:
            from hive_mcp.server import build_server
            server = build_server()
            fn = server._tool_manager._tools["ticket_route_expedited"].fn
            (tmp_company_dir / "logs").mkdir(exist_ok=True)
            result = fn(
                type="bug",
                priority="critical",
                title="DB connection pool exhausted",
                description="Production blocker: pool saturates under load",
                created_by="VP-RD",
                target_department="rd",
                company_dir=str(tmp_company_dir),
            )
            assert result.get("ok") is True, result
            tickets = list((tmp_company_dir / "departments" / "rd" / "tickets" / "backlog").glob("*.json"))
            assert len(tickets) == 1, f"expected 1 expedited ticket, got {len(tickets)}"
            data = json.loads(tickets[0].read_text())
            assert data["title"] == "DB connection pool exhausted"
            assert data["source"] == "expedited-routing"
        finally:
            if old_path:
                os.environ["PYTHONPATH"] = old_path
            else:
                os.environ.pop("PYTHONPATH", None)

    def test_ticket_create_stores_labels_and_acceptance_criteria(self, install_dir, tmp_company_dir):
        """C.10: ticket_create must forward labels / acceptance_criteria to
        ticket-create.sh (--labels/--ac) — the dashboard create form feeds
        them from user input, and they used to be silently dropped at the
        API boundary."""
        sys_path = str(install_dir / "lib")
        old_path = os.environ.get("PYTHONPATH", "")
        if sys_path not in old_path:
            os.environ["PYTHONPATH"] = sys_path + (":" + old_path if old_path else "")

        try:
            from hive_mcp.server import build_server
            server = build_server()
            fn = server._tool_manager._tools["ticket_create"].fn
            (tmp_company_dir / "logs").mkdir(exist_ok=True)
            result = fn(
                dept="it",
                type="feature",
                priority="medium",
                title="Labels + AC round trip",
                description="desc",
                labels=["auth", "api"],
                acceptance_criteria=["one", "two"],
                company_dir=str(tmp_company_dir),
            )
            assert result.get("ok") is True, result
            files = list((tmp_company_dir / "departments" / "it" / "tickets" / "backlog").glob("*.json"))
            assert len(files) == 1
            data = json.loads(files[0].read_text())
            assert data["labels"] == ["auth", "api"]
            assert data["acceptance_criteria"] == ["one", "two"]
        finally:
            if old_path:
                os.environ["PYTHONPATH"] = old_path
            else:
                os.environ.pop("PYTHONPATH", None)

def _is_pure_shim(text):
    """A deprecation shim: a straight-line script that echoes a notice and
    exits non-zero — no conditionals, no arg reading, no real work. Any
    caller of such a script fails unconditionally, so an MCP tool that
    targets one is dead on arrival."""
    code = []
    for line in text.splitlines():
        s = line.strip()
        if (not s or s.startswith("#") or s.startswith("set ")
                or s.startswith("source ") or s.startswith(". ")):
            continue
        if s.startswith("echo ") or s.startswith("echo\t"):
            continue
        code.append(s)
    if not code:
        return False
    return all(re.match(r"^exit\s+\d+\s*$", s) for s in code)


def _single_line_required_args(sh_text):
    """Required positional count from a single-line Usage: form, or None
    when the usage is multi-line / subcommand-style (undecidable → skip).
    <required> inside [optional] groups does not count."""
    for line in sh_text.splitlines():
        s = line.strip().lstrip("#").strip()
        if not s.startswith("Usage:"):
            continue
        u = s.split("Usage:", 1)[1]
        if "|" in u:
            return None
        return len(re.findall(r"<[^>]+>", re.sub(r"\[[^\]]*\]", "", u)))
    return None


def _mcp_run_calls(tools_dir):
    """Yield a description of each run("script", ...) call in the MCP tool
    modules: dict(module, line, script, pos, conds, flags). `pos` counts
    positional args in the base args list (and direct positional args),
    `conds` counts args.append(...) calls (conditional positionals),
    `flags` collects literal --flag args."""
    import ast

    for py in sorted(tools_dir.glob("*.py")):
        tree = ast.parse(py.read_text())
        funcs = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
        seen = set()
        for call in ast.walk(tree):
            if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                    and call.func.id == "run" and call.args
                    and isinstance(call.args[0], ast.Constant)
                    and isinstance(call.args[0].value, str)):
                continue
            fn = None
            for f in funcs:
                if f.lineno <= call.lineno <= f.end_lineno:
                    if fn is None or f.lineno > fn.lineno:
                        fn = f
            key = (fn.name if fn else "?", call.lineno)
            if key in seen:
                continue
            seen.add(key)
            pos, flags, conds = 0, [], 0
            for a in call.args[1:]:
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    if a.value.startswith("--"):
                        flags.append(a.value[2:])
                    else:
                        pos += 1
                elif isinstance(a, ast.Name):
                    pos += 1
                elif isinstance(a, ast.Starred) and fn is not None:
                    name = a.value.id if isinstance(a.value, ast.Name) else None
                    if not name:
                        continue
                    for node in ast.walk(fn):
                        if (isinstance(node, ast.Assign)
                                and any(isinstance(t, ast.Name) and t.id == name
                                        for t in node.targets)
                                and isinstance(node.value, ast.List)):
                            for e in node.value.elts:
                                if (isinstance(e, ast.Constant)
                                        and isinstance(e.value, str)):
                                    if e.value.startswith("--"):
                                        flags.append(e.value[2:])
                                    else:
                                        pos += 1
                                elif isinstance(e, ast.Name):
                                    pos += 1
                        if (isinstance(node, ast.Call)
                                and isinstance(node.func, ast.Attribute)
                                and node.func.attr == "append"
                                and isinstance(node.func.value, ast.Name)
                                and node.func.value.id == name):
                            conds += 1
            yield {
                "module": py.name, "line": call.lineno,
                "script": call.args[0].value,
                "pos": pos, "conds": conds, "flags": flags,
            }


@pytest.mark.skipif(not _HAS_FASTMCP, reason="mcp not available")
class TestMCPToolsDoc:
    """D.12: share/docs/MCP-TOOLS.md must stay in sync with the live server.

    The doc is generated by packaging/gen-mcp-tools-doc.py (name +
    docstring + signature of every registered tool). Hand edits drift —
    the last drift left removed tools (company_start/stop) documented
    and the count wrong (49 vs 55)."""

    def test_mcp_tools_doc_matches_live_server(self):
        import subprocess
        gen = Path(__file__).resolve().parent.parent / "packaging" / "gen-mcp-tools-doc.py"
        assert gen.is_file(), "packaging/gen-mcp-tools-doc.py missing"
        out = subprocess.run(
            [sys.executable, str(gen), "--stdout"],
            capture_output=True, text=True, timeout=120,
        )
        assert out.returncode == 0, out.stderr[-500:]
        doc = Path(__file__).resolve().parent.parent / "share" / "docs" / "MCP-TOOLS.md"
        assert doc.is_file(), "share/docs/MCP-TOOLS.md missing"
        current = doc.read_text()
        if current != out.stdout:
            import difflib
            diff = "\n".join(difflib.unified_diff(
                current.splitlines(), out.stdout.splitlines(),
                "checked-in MCP-TOOLS.md", "generated", lineterm=""))
            assert False, (
                "share/docs/MCP-TOOLS.md has drifted from the live server.\n"
                "Regenerate: python3 packaging/gen-mcp-tools-doc.py\n\n"
                + diff[:4000])


@pytest.mark.skipif(not _HAS_FASTMCP, reason="mcp not available")
class TestMCPToolScriptContract:
    """C.9 audit guard: no MCP tool may be dead on arrival.

    ticket_route_expedited had a wrapper/signature mismatch (wrong arg
    order) plus a wrong SKILLS_DIR default. This class systematically
    diffs every run("script", ...) call in lib/hive_mcp/tools/ against the
    underlying lib/tools/*.sh contract:
      1. the target script exists and is not a deprecation shim;
      2. every --flag the wrapper passes is accepted by the script;
      3. for single-line-Usage scripts, the wrapper can supply at least the
         required positionals (base list + conditional appends).
    """

    @pytest.fixture()
    def tools_dir(self, install_dir):
        return install_dir / "lib" / "hive_mcp" / "tools"

    def test_run_targets_exist_and_are_not_shims(self, tools_dir, install_dir):
        problems = []
        for c in _mcp_run_calls(tools_dir):
            sh = install_dir / "lib" / "tools" / (c["script"] + ".sh")
            if not sh.is_file():
                problems.append(f"{c['module']}:{c['line']} -> missing {sh}")
            elif _is_pure_shim(sh.read_text()):
                problems.append(f"{c['module']}:{c['line']} -> {c['script']}.sh "
                                "is a deprecation shim (always exits non-zero)")
        assert not problems, "dead-on-arrival MCP tools:\n" + "\n".join(problems)

    def test_flags_passed_are_accepted_by_scripts(self, tools_dir, install_dir):
        problems = []
        for c in _mcp_run_calls(tools_dir):
            sh = install_dir / "lib" / "tools" / (c["script"] + ".sh")
            if not sh.is_file():
                continue  # covered by the existence test
            src = sh.read_text()
            for fl in c["flags"]:
                if f"--{fl}" not in src:
                    problems.append(f"{c['module']}:{c['line']} passes --{fl} "
                                    f"but {c['script']}.sh never mentions it")
        assert not problems, "unaccepted flags:\n" + "\n".join(problems)


    def test_required_positionals_covered(self, tools_dir, install_dir):
        problems = []
        for c in _mcp_run_calls(tools_dir):
            sh = install_dir / "lib" / "tools" / (c["script"] + ".sh")
            if not sh.is_file():
                continue
            req = _single_line_required_args(sh.read_text())
            if req is None or req == 0:
                continue  # multi-line / subcommand usage: undecidable
            if c["pos"] + c["conds"] < req:
                problems.append(f"{c['module']}:{c['line']} -> {c['script']}.sh "
                                f"requires {req} positionals, wrapper supplies "
                                f"at most {c['pos'] + c['conds']}")
        assert not problems, "missing required args:\n" + "\n".join(problems)

def _free_local_port() -> int:
    """Ask the kernel for a currently-free loopback port (xdist-safe: each
    worker/test asks for its own port, so parallel workers never collide)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_port(port: int, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.2)
    return False


@pytest.fixture(scope="module")
def http_mcp_server():
    """Boot the REAL MCP server (`server.py --transport http`) on an ephemeral
    port — the same command `hive start` / `hive dashboard` run — and yield
    the port. Terminates the server on teardown."""
    install_dir = Path(__file__).resolve().parent.parent
    port = _free_local_port()
    env = {
        **os.environ,
        "PYTHONPATH": str(install_dir / "lib"),
        "HIVE_ROOT": str(install_dir),
        "HIVE_INSTALL_DIR": str(install_dir),
    }
    log_path = tempfile.mktemp(prefix="hive-mcp-test-", suffix=".log")
    log_file = open(log_path, "w")
    proc = subprocess.Popen(
        [sys.executable, str(install_dir / "lib" / "hive_mcp" / "server.py"),
         "--transport", "http", "--host", "127.0.0.1", "--port", str(port)],
        env=env, cwd=str(install_dir),
        stdout=log_file, stderr=subprocess.STDOUT,
    )
    try:
        if not _wait_for_port(port, timeout=30.0):
            proc.kill()
            log_file.close()
            try:
                log = Path(log_path).read_text()
            except OSError:
                log = "(log unreadable)"
            pytest.fail(f"MCP HTTP server did not open port {port}; log:\n{log}")
        yield port
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log_file.close()
        try:
            os.unlink(log_path)
        except OSError:
            pass


def _mcp_client_on_port(port: int, monkeypatch):
    """Point the module-level mcp_client (host/port are read at call time)
    at the given loopback port."""
    from hive_cli import mcp_client
    monkeypatch.setattr(mcp_client, "MCP_HOST", "127.0.0.1")
    monkeypatch.setattr(mcp_client, "MCP_PORT", port)
    return mcp_client


@pytest.mark.skipif(not _HAS_FASTMCP, reason="mcp server/client not available")


class TestHttpTransportWire:
    """C.5: the streamable-http path every operator actually uses. The CLI and
    the dashboard both reach the MCP server over `--transport http` (default
    port 7891); the suite previously only exercised in-process tool
    registration, so this class boots the real server and round-trips tools
    over the wire."""

    def test_write_then_read_roundtrip(self, http_mcp_server, tmp_company_dir,
                                       monkeypatch):
        """A write tool (ticket_create) and a read tool (ticket_get) both
        round-trip over HTTP, and the write really landed on disk."""
        mcp_client = _mcp_client_on_port(http_mcp_server, monkeypatch)
        company_dir = str(tmp_company_dir)
        title = "wire round-trip ticket"

        created = mcp_client.call_tool("ticket_create", {
            "dept": "it", "type": "chore", "priority": "low",
            "title": title,
            "description": "Created over the streamable-http wire",
            "company_dir": company_dir,
        })
        assert created.get("ok") is True, created
        # ticket-create.sh prints the bare ticket id to stdout.
        ticket_id = (created.get("stdout") or "").strip() or (
            (created.get("data") or {}).get("id", ""))
        assert ticket_id, f"no ticket id in create response: {created}"

        got = mcp_client.call_tool("ticket_get", {
            "dept": "it", "ticket_id": ticket_id, "company_dir": company_dir,
        })
        assert got.get("ok") is True, got
        data = got.get("data") or {}
        assert data.get("id") == ticket_id
        assert data.get("title") == title

        # The write really landed in the tmp company dir (not some CWD).
        tickets_root = Path(company_dir) / "departments" / "it" / "tickets"
        found = list(tickets_root.rglob(f"{ticket_id}.json"))
        assert found, f"{ticket_id}.json not found under {tickets_root}"
        assert json.loads(found[0].read_text())["title"] == title

    def test_list_tools_over_http(self, http_mcp_server, monkeypatch):
        mcp_client = _mcp_client_on_port(http_mcp_server, monkeypatch)
        tools = mcp_client.list_tools()
        assert "ticket_create" in tools
        assert "ticket_get" in tools
        assert len(tools) >= 40, (
            f"expected >= 40 tools over the wire, got {len(tools)}")

    def test_in_process_matches_http(self, http_mcp_server, tmp_company_dir,
                                     monkeypatch):
        """T1 (0.20) transport identity: the same tool over a live HTTP
        server and in-process returns EQUAL dicts. The in-process rung is
        the same function object the server exposes (one implementation,
        no transport) — this pin keeps that true as both paths age."""
        live = _mcp_client_on_port(http_mcp_server, monkeypatch)
        args = {"company_dir": str(tmp_company_dir)}
        over_wire = live.call_tool("roster_get", dict(args))
        dead = _mcp_client_on_port(_free_local_port(), monkeypatch)
        in_proc = dead.call_tool("roster_get", dict(args))
        assert over_wire.get("ok") is True, over_wire
        assert in_proc.get("ok") is True, in_proc
        assert over_wire == in_proc, (
            "HTTP and in-process must return the same tool dict — one "
            "implementation, no transport")

    def test_in_process_drops_unknown_keys_like_the_wire(self,
                                                         http_mcp_server,
                                                         tmp_company_dir,
                                                         monkeypatch):
        """T1 (0.20): the wire validates against the tool's parameter
        schema (pydantic, extra ignored) — an unknown key is DROPPED,
        not an error. The historical case: the CLI's ticket_move payload
        has always carried `force` (the argparse flag; the tool has no
        such parameter) — the in-process rung must filter identically, or
        a stopped company's `hive ticket move` dies with a TypeError the
        wire never showed."""
        live = _mcp_client_on_port(http_mcp_server, monkeypatch)
        dead = _mcp_client_on_port(_free_local_port(), monkeypatch)
        # A ticket that does not exist: both rungs surface the tool's own
        # error face (no mutation, deterministic) — with the unknown
        # `force` key in the payload.
        args = {"dept": "it", "ticket_id": "TICKET-001",
                "new_status": "sprint-current", "comment": "c",
                "author": "u", "force": False,
                "company_dir": str(tmp_company_dir)}
        over_wire = live.call_tool("ticket_move", dict(args))
        in_proc = dead.call_tool("ticket_move", dict(args))
        assert "TypeError" not in json.dumps(in_proc), (
            "in-process passed the unknown `force` key to the tool "
            "function — mirror the wire's schema validation (drop "
            "unknown keys) or a stopped company's move breaks")
        assert over_wire == in_proc, (
            "HTTP and in-process must treat the unknown key the same "
            "way (the wire drops it): "
            f"{over_wire} != {in_proc}")

    def test_server_down_and_sdk_free_still_runs_in_process(
            self, http_mcp_server, tmp_company_dir, monkeypatch):
        """T1 (0.22): the in-process rung no longer needs the SDK —
        server down AND SDK missing still EXECUTES the tool (the module
        is imported through the duck-typed collector), and the missing
        ticket surfaces the tool's own error face. The 0.20 ladder
        envelope ("in-process fallback unavailable") is gone: it was
        the symptom of the SDK import the rung used to pay.
        Envelope invariance: the same call returns the SAME dict the
        wire and the SDK-present in-process rungs return (one
        implementation)."""
        live = _mcp_client_on_port(http_mcp_server, monkeypatch)
        dead = _mcp_client_on_port(_free_local_port(), monkeypatch)
        assert dead._is_http_server_up() is False

        # Make the SDK unresolvable (the SDK-free environment).
        monkeypatch.setattr(dead, "_sdk_available", lambda: False)

        args = {"dept": "it", "ticket_id": "TICKET-001",
                "company_dir": str(tmp_company_dir)}
        res = dead.call_tool("ticket_get", dict(args))
        over_wire = live.call_tool("ticket_get", dict(args))
        assert res.get("ok") is False, res
        assert "in-process fallback unavailable" not in json.dumps(res)
        assert "MCP HTTP server not reachable" not in json.dumps(res)
        assert "TypeError" not in json.dumps(res)
        assert res == over_wire, (
            "server-down + SDK-free must return the SAME envelope as "
            "the wire (the tool's own error face): "
            f"{res} != {over_wire}")

    def test_unknown_tool_is_a_named_defect(self, tmp_company_dir,
                                            monkeypatch):
        """T1 (0.22): rung 3 is a real defect, not the default path —
        a name no tool module provides returns the house error
        envelope with the rung named (module import failure / missing
        tool), never a crash and never a silent empty success."""
        dead = _mcp_client_on_port(_free_local_port(), monkeypatch)
        res = dead.call_tool("no_such_tool",
                             {"company_dir": str(tmp_company_dir)})
        assert res["ok"] is False, res
        assert "in-process tool resolution failed" in res["error"], res
        assert "no_such_tool" in res["error"], res


class TestStdioSpawnRetired:
    """T1 (0.20) static surface pin (the house surface-guard pattern, the
    shape of TestTomliSurfaceGuard): the stdio spawn is RETIRED from the
    CLI client. "StdioServerParameters" / "stdio_client" / _stdio_session
    absent from mcp_client.py means no code path can bring the one-shot
    child (and its per-spawn NOTE line) back. The stdio SERVER transport
    (hive_mcp/server.py --transport stdio) is a different surface — its
    consumers never go through mcp_client, so it stays pinned by the
    server-side tests (the AGENTS.md contract)."""

    def test_mcp_client_carries_no_stdio_spawn(self):
        src = (Path(__file__).resolve().parent.parent
               / "lib" / "hive_cli" / "mcp_client.py").read_text()
        for needle in ("StdioServerParameters", "stdio_client",
                       "_stdio_session", "_stdio_python"):
            assert needle not in src, (
                f"{needle!r} in mcp_client.py — the stdio spawn was "
                "retired from the CLI in 0.20 T1 (the in-process rung "
                "replaced it); do not re-introduce the one-shot child")

    def test_stdio_server_transport_stays(self):
        """The transport itself did not move: the server still declares
        --transport stdio (programmatic consumers keep it; only the CLI
        stops being one)."""
        src = (Path(__file__).resolve().parent.parent
               / "lib" / "hive_mcp" / "server.py").read_text()
        assert '"stdio"' in src or "'stdio'" in src, (
            "server.py no longer declares the stdio transport — the "
            "programmatic consumers (scripts, tests, interactive "
            "sessions) keep it; only the CLI's use of it retired")


@pytest.mark.skipif(not _HAS_FASTMCP, reason="mcp server not available")
class TestMcpClientSdkFree:
    """C.2: mcp_client falls back to the shared SDK-free HTTP client
    (lib/core/mcp_http.py) when the mcp SDK is not importable — the
    SDK-backed fast path is untouched when the SDK is present."""

    def test_call_tool_with_sdk_uninstalled(self, http_mcp_server, tmp_company_dir):
        repo = Path(__file__).resolve().parent.parent
        poison = Path(tempfile.mkdtemp(prefix="hive-poison-"))
        (poison / "mcp.py").write_text(
            "raise ImportError('mcp SDK intentionally absent')\n")
        code = (
            "import sys\n"
            f"sys.path.insert(0, {str(repo / 'lib')!r})\n"
            f"sys.path.insert(0, {str(poison)!r})\n"
            "from hive_cli import mcp_client\n"
            "assert mcp_client._sdk_available() is False\n"
            f"mcp_client.MCP_HOST = '127.0.0.1'\n"
            f"mcp_client.MCP_PORT = {http_mcp_server}\n"
            "res = mcp_client.call_tool('ticket_list', "
            f"{{'dept': 'it', 'company_dir': {str(tmp_company_dir)!r}}})\n"
            "assert res.get('ok') is True, res\n"
            "assert res.get('data', {}).get('count') == 0, res\n"
            "tools = mcp_client.list_tools()\n"
            "assert 'ticket_create' in tools and len(tools) >= 40, len(tools)\n"
            "print('SDKFREE_OK')\n"
        )
        r = subprocess.run([sys.executable, "-c", code],
                           capture_output=True, text=True, timeout=90,
                           env={**os.environ, "PYTHONPATH": str(repo / "lib")})
        assert r.returncode == 0, f"{r.stdout[-400:]} {r.stderr[-800:]}"
        assert "SDKFREE_OK" in r.stdout


class TestSdkPoisonedRoundTrip:
    """T1 (0.22) — the severance, proven at the process boundary.

    The mcp SDK is poisoned (a fake ``mcp.py`` that raises ImportError,
    first on PYTHONPATH — the mcp.py poison pattern from
    test_harness_tool_loop), the MCP server is DOWN, and the read face
    (status, roster, ticket list, backlog, gates, release status)
    returns envelopes BYTE-IDENTICAL to a SDK-importable process —
    and ``mcp`` never lands in sys.modules (asserted on sys.modules,
    not on the poison's side effect). Subprocess isolation: the
    poisoning is env-level, and the xdist worker's own sys.modules may
    already carry the SDK (the server-side tests import it) — the
    contract is per-process, so each leg runs in a fresh interpreter
    under the same env contract (tests/test_hive_paths.py is the
    pattern). Wall-clock keys (roster.json's updated_at) are compared
    through conftest.normalize_clock — identical STATE, not identical
    INSTANT (the xdist worker contract, R1).
    """

    REPO = Path(__file__).resolve().parent.parent

    #: The read face — (label, tool, static args); company_dir is
    #: injected at run time.
    FACES = [
        ("status", "status_get", {}),
        ("roster", "roster_get", {}),
        ("ticket list", "ticket_list", {}),
        ("backlog", "ticket_list", {"status": "backlog"}),
        ("gates", "gates_check", {"dept": "it", "ticket_id": "T-1"}),
        ("release status", "release_status", {}),
    ]

    @staticmethod
    def _seed(company: Path):
        """A static company: every face reads files (or a deterministic
        rebuild of them), so both legs read the same bytes — the
        envelopes differ in no key but the roster rebuild's wall
        clock (handled by normalize_clock at the assert)."""
        depts = company / "departments" / "it" / "tickets"
        for status in ("backlog", "sprint-current", "in-progress",
                       "in-review", "done"):
            (depts / status).mkdir(parents=True, exist_ok=True)
        ticket = {
            "id": "T-1", "title": "poison round-trip ticket",
            "type": "chore", "priority": "low", "status": "backlog",
            "created_by": "t1", "source": "manual",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
        (depts / "backlog" / "T-1.json").write_text(
            json.dumps(ticket, indent=2))
        (company / "departments" / "it" / "status.json").write_text(
            json.dumps({"dept": "it", "updated_at": "2026-01-01T00:00:00Z",
                        "tickets": {"total": 1,
                                    "by_status": {"backlog": 1}}},
                       indent=2))
        sprints = company / "sprints"
        sprints.mkdir(exist_ok=True)
        (sprints / "current.json").write_text(json.dumps(
            {"sprint_id": "sprint-1", "stage": "tdd-red",
             "release": {"version": "0.0.0", "state": "idle"}}, indent=2))

    def _run_leg(self, company: Path, poisoned: bool, tool: str,
                 args: dict, scratch: Path) -> str:
        """One leg in a fresh interpreter; returns the printed envelope
        line. The poisoned leg additionally prints NOMCP_OK after
        asserting 'mcp' out of sys.modules. The poison dir is a
        tmp_path child (the house contract: no per-test mktemp
        litter — the reaper pins the module-level sites only)."""
        poison = scratch / ("poison" if poisoned else "noop")
        poison.mkdir(parents=True, exist_ok=True)
        if poisoned:
            (poison / "mcp.py").write_text(
                "raise ImportError('mcp SDK intentionally absent')\n")
        extra = ("assert 'mcp' not in sys.modules, "
                 "'mcp landed in sys.modules on the stopped read'\n"
                 "print('NOMCP_OK')\n") if poisoned else ""
        code = (
            "import sys, json\n"
            f"sys.path.insert(0, {str(self.REPO / 'lib')!r})\n"
            "from hive_cli import mcp_client\n"
            f"res = mcp_client.call_tool({tool!r}, {json.dumps(args)})\n"
            "print(json.dumps(res, sort_keys=True))\n"
            + extra
        )
        pypath = os.environ.get("PYTHONPATH", "")
        if poisoned:
            pypath = (str(poison) + os.pathsep + pypath) if pypath \
                else str(poison)
        r = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, timeout=60,
            cwd=str(company.parent),
            env={**os.environ, "PYTHONPATH": pypath})
        assert r.returncode == 0, (
            f"{tool} ({'poisoned' if poisoned else 'clean'}) leg failed:\n"
            f"{r.stdout[-400:]}\n{r.stderr[-800:]}")
        lines = r.stdout.strip().splitlines()
        if poisoned:
            assert lines and lines[-1] == "NOMCP_OK", (
                "the poisoned leg must assert 'mcp' out of sys.modules "
                "(not merely that the poison raised): " + r.stdout[-200:])
            return lines[-2]
        return lines[-1]

    @pytest.mark.parametrize("label,tool,static_args", FACES,
                             ids=[f[0] for f in FACES])
    def test_poisoned_envelope_matches_clean(self, tmp_company_dir,
                                             tmp_path, label, tool,
                                             static_args):
        self._seed(tmp_company_dir)
        args = {**static_args, "company_dir": str(tmp_company_dir)}
        clean = json.loads(
            self._run_leg(tmp_company_dir, False, tool, args, tmp_path))
        poisoned = json.loads(
            self._run_leg(tmp_company_dir, True, tool, args, tmp_path))
        assert normalize_clock(clean) == normalize_clock(poisoned), (
            f"{label}: SDK-poisoned envelope must equal the SDK-"
            "importable envelope (byte-identical modulo the shared "
            f"clock keys): {clean} != {poisoned}")


@pytest.mark.skipif(not _HAS_FASTMCP, reason="mcp SDK not available")
class TestTicketListCompanyWideTool:
    """T2 (0.17): ticket_list's department is optional — with none it
    lists the whole company (the script's company-wide doc)."""

    @staticmethod
    def _sig(name):
        import inspect
        from hive_mcp.server import build_server
        return inspect.signature(build_server()._tool_manager._tools[name].fn)

    def test_dept_is_optional(self):
        sig = self._sig("ticket_list")
        p = sig.parameters["dept"]
        assert p.default is None

    def test_docstring_names_the_company_wide_view(self):
        import inspect
        from hive_mcp.server import build_server
        fn = build_server()._tool_manager._tools["ticket_list"].fn
        doc = inspect.getdoc(fn) or ""
        assert "company-wide" in doc
        assert "departments" in doc
