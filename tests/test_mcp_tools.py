"""Tests for hive_mcp.tools — MCP tool registration and behavior."""
import sys
import re
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
                "agent_spawn_ceo", "agent_run_vp",
                "gates_run", "gates_check",
                "status_get",
                "round_get_current", "round_enqueue", "round_advance",
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
            # The small-model (llm-tiny) tools register under their
            # functional names, not an llm_* prefix.
            for tool in ("classify_ticket", "triage_test_failure",
                         "narrate_status", "summarize_thread"):
                assert tool in tools, f"Expected llm-tiny tool '{tool}' not found"
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

    def test_round_promote_next_passes_project_root(self, install_dir, tmp_path, monkeypatch):
        """C.9: round_promote_next called round-promote.sh with no args; the
        script defaults PROJECT_ROOT to CWD and re-derives COMPANY_DIR from
        it (overriding the env), so the company_dir parameter was silently
        ignored and the promote would hit the MCP server's CWD project
        instead of the target. The wrapper must pass the project root."""
        proj = tmp_path / "proj"
        rounds = proj / ".company" / "rounds"
        (rounds / "history").mkdir(parents=True)
        (rounds / "current-round.json").write_text(json.dumps({
            "round_id": "round-old-1", "sprint_id": None, "started_at": "t",
            "ended_at": None, "source": "pending", "cursor": 0, "entries": [],
        }))
        # CWD must NOT be the project root, or the buggy no-arg path works.
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)

        sys_path = str(install_dir / "lib")
        old_path = os.environ.get("PYTHONPATH", "")
        if sys_path not in old_path:
            os.environ["PYTHONPATH"] = sys_path + (":" + old_path if old_path else "")
        try:
            from hive_mcp.server import build_server
            server = build_server()
            fn = server._tool_manager._tools["round_promote_next"].fn
            result = fn(company_dir=str(proj / ".company"))
            assert result.get("ok") is True, result
            # The old round was archived in the TARGET project ...
            assert (rounds / "history" / "round-old-1.json").exists(), (
                "round was not promoted in the target project "
                f"(stderr: {result.get('stderr', '')[:200]})"
            )
            # ... and a fresh shell round replaced it there.
            cur = json.loads((rounds / "current-round.json").read_text())
            assert cur["round_id"] != "round-old-1"
        finally:
            if old_path:
                os.environ["PYTHONPATH"] = old_path
            else:
                os.environ.pop("PYTHONPATH", None)
