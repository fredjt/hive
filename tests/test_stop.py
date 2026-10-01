"""Tests verifying that stop only kills daemons, not the dashboard or MCP."""
from pathlib import Path


def get_install_dir():
    """Return the hive install directory (absolute path)."""
    return Path(__file__).resolve().parent.parent


class TestCompanyStopDoesNotKillDashboard:
    """company-stop.sh should NOT kill the dashboard.

    The dashboard is the user's UI — stopping the company should stop daemons
    but leave the dashboard running so the user can start again later without
    reopening the browser."""

    def test_company_stop_does_not_kill_dashboard(self):
        """company-stop.sh must not have a pattern that kills dashboard/server.js."""
        cs = get_install_dir() / "lib" / "tools" / "company-stop.sh"
        content = cs.read_text()
        lines = content.split('\n')

        # Collect all pattern strings (inside the PATTERNS array)
        patterns = []
        in_patterns = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('PATTERNS=('):
                in_patterns = True
                continue
            if in_patterns and stripped == ')':
                in_patterns = False
                continue
            if in_patterns and '"' in stripped:
                # Extract the pattern string between quotes
                for part in stripped.split('"'):
                    if part and not part.startswith('#') and not part.startswith('//'):
                        patterns.append(part)

        # None of the patterns should match dashboard/server.js
        for pat in patterns:
            assert 'dashboard/server.js' not in pat, (
                f"BUG: company-stop.sh kills the dashboard via pattern: {pat}. "
                "Stopping the company should NOT kill the dashboard — it's the user's UI."
            )

    def test_company_stop_does_not_kill_mcp_server(self):
        """company-stop.sh must not have a pattern that kills the MCP server.

        The MCP server is a shared resource used by the dashboard for tool calls.
        Killing it would break the dashboard after stop."""
        cs = get_install_dir() / "lib" / "tools" / "company-stop.sh"
        content = cs.read_text()
        lines = content.split('\n')

        # Collect all pattern strings
        patterns = []
        in_patterns = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('PATTERNS=('):
                in_patterns = True
                continue
            if in_patterns and stripped == ')':
                in_patterns = False
                continue
            if in_patterns and '"' in stripped:
                for part in stripped.split('"'):
                    if part and not part.startswith('#') and not part.startswith('//'):
                        patterns.append(part)

        # None should match hive_mcp/server.py
        for pat in patterns:
            assert 'hive_mcp/server.py' not in pat, (
                f"BUG: company-stop.sh kills the MCP server via pattern: {pat}. "
                "The MCP server is a shared resource — the dashboard needs it for tool calls."
            )

    def test_company_stop_still_kills_daemons(self):
        """company-stop.sh must still kill all company daemons."""
        cs = get_install_dir() / "lib" / "tools" / "company-stop.sh"
        content = cs.read_text()
        lines = content.split('\n')

        patterns = []
        in_patterns = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('PATTERNS=('):
                in_patterns = True
                continue
            if in_patterns and stripped == ')':
                in_patterns = False
                continue
            if in_patterns and '"' in stripped:
                for part in stripped.split('"'):
                    if part and not part.startswith('#') and not part.startswith('//'):
                        patterns.append(part)

        # Must still have daemon kill patterns
        all_patterns_str = ' '.join(patterns)
        assert 'sprint-lifecycle' in all_patterns_str, "Must kill sprint-lifecycle"
        assert 'roster-rebuild' in all_patterns_str, "Must kill roster-rebuild"
        assert 'status-rebuild' in all_patterns_str, "Must kill status-rebuild"
        assert 'worktree-merger' in all_patterns_str, "Must kill worktree-merger"

    def test_company_stop_still_kills_agent_spawns(self):
        """company-stop.sh must still kill running agent harness spawns."""
        cs = get_install_dir() / "lib" / "tools" / "company-stop.sh"
        content = cs.read_text()
        lines = content.split('\n')

        patterns = []
        in_patterns = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('PATTERNS=('):
                in_patterns = True
                continue
            if in_patterns and stripped == ')':
                in_patterns = False
                continue
            if in_patterns and '"' in stripped:
                for part in stripped.split('"'):
                    if part and not part.startswith('#') and not part.startswith('//'):
                        patterns.append(part)

        all_patterns_str = ' '.join(patterns)
        assert 'agent-spawn' in all_patterns_str, "Must kill agent-spawn processes"
        assert 'agent-harness' in all_patterns_str, "Must kill agent-harness processes"


class TestCompanyStopAncestorProtection:
    """company-stop.sh must protect its own process tree."""

    def test_ancestor_exclusion_exists(self):
        """company-stop.sh must build and check ancestor PIDs."""
        cs = get_install_dir() / "lib" / "tools" / "company-stop.sh"
        content = cs.read_text()
        assert "ANCESTOR_PIDS" in content
        assert "is_ancestor" in content

    def test_company_start_calls_stop_first(self):
        """company-start.sh must call company-stop.sh first to clean stale state."""
        cs = get_install_dir() / "lib" / "tools" / "company-start.sh"
        content = cs.read_text()
        assert "company-stop.sh" in content
