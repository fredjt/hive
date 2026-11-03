"""Tests for the startup flow: hive (no args) → dashboard only, Start Company → daemons."""
from pathlib import Path


def get_install_dir():
    """Return the hive install directory (absolute path)."""
    return Path(__file__).resolve().parent.parent


class TestHiveNoArgsStartsDashboardOnly:
    """Verify that `hive` with no arguments starts only the dashboard + MCP,
    not the company daemons."""

    def test_bin_hive_delegates_to_cli_main(self):
        """bin/hive should exec the Python CLI entry point."""
        install_dir = get_install_dir()
        bin_hive = install_dir / "bin" / "hive"
        content = bin_hive.read_text()
        assert "hive_cli.main" in content

    def test_main_no_args_calls_dashboard_handle(self):
        """main.py: bare `hive` (no args) should call dashboard.handle()."""
        main_py = get_install_dir() / "lib" / "hive_cli" / "main.py"
        content = main_py.read_text()
        # The key line: if not args.cmd → dashboard.handle()
        assert "if not args.cmd:" in content
        assert "dashboard.handle" in content

    def test_dashboard_handle_starts_mcp_and_dashboard(self):
        """dashboard.py handle() should start MCP server and Node dashboard."""
        dashboard_py = get_install_dir() / "lib" / "hive_cli" / "commands" / "dashboard.py"
        content = dashboard_py.read_text()
        assert "hive_mcp" in content  # starts MCP server
        assert "server.js" in content  # starts Node dashboard (via HIVE_INSTALL_DIR path)
        # Should NOT call company-start.sh
        assert "company-start" not in content

    def test_company_start_sh_starts_daemons(self):
        """company-start.sh should start daemons (sprint-lifecycle, rebuilders)."""
        cs = get_install_dir() / "lib" / "tools" / "company-start.sh"
        content = cs.read_text()
        assert "sprint-lifecycle" in content
        assert "roster-rebuild" in content
        assert "status-rebuild" in content

    def test_dashboard_has_start_company_button(self):
        """Dashboard server.js should have a /api/company/start endpoint."""
        server_js = get_install_dir() / "lib" / "dashboard" / "server.js"
        content = server_js.read_text()
        assert "/api/company/start" in content

    def test_company_start_sh_calls_via_dashboard_api(self):
        """The dashboard's start endpoint should call company-start.sh."""
        server_js = get_install_dir() / "lib" / "dashboard" / "server.js"
        content = server_js.read_text()
        assert "company-start.sh" in content


class TestStartupRobustness:
    """Tests for robustness of the startup flow."""

    def test_company_start_sh_detects_running_mcp(self):
        """company-start.sh should detect an already-running MCP server and skip starting it."""
        cs = get_install_dir() / "lib" / "tools" / "company-start.sh"
        content = cs.read_text()
        # Should check if MCP is already running before trying to start it
        assert "MCP server already running" in content

    def test_company_start_sh_detects_running_dashboard(self):
        """company-start.sh should detect an already-running dashboard on port 3456
        and NOT start a duplicate."""
        cs = get_install_dir() / "lib" / "tools" / "company-start.sh"
        content = cs.read_text()
        # After fix: should check if something is already on the dashboard port
        # before killing it. Look for a guard like "dashboard already running".
        assert "DASHBOARD_PORT" in content

    def test_company_start_sh_does_not_unconditionally_kill_dashboard(self):
        """company-start.sh should NOT kill an existing dashboard (e.g., started by `hive`).
        
        When `hive` is running with no args, it starts the dashboard on port 3456.
        Clicking "Start Company" in that dashboard should NOT kill and restart it —
        it should only start the daemons."""
        cs = get_install_dir() / "lib" / "tools" / "company-start.sh"
        content = cs.read_text()
        
        lines = content.split('\n')
        
        # Find line numbers for key events
        free_port_call_line = None  # The actual call (not definition)
        dashboard_start_line = None
        
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith('#'):
                continue
            # Match the actual call: free_port "$DASHBOARD_PORT" or similar
            if 'free_port' in stripped and '(' not in stripped and 'local' not in stripped:
                free_port_call_line = i
            # Match dashboard server.js start
            if 'dashboard/server.js' in stripped:
                dashboard_start_line = i
        
        assert free_port_call_line is not None, "Could not find free_port() call"
        assert dashboard_start_line is not None, "Could not find dashboard/server.js start"
        
        # The bug: free_port is called unconditionally (no if guard) before starting the dashboard.
        # Check if there's an 'if' or 'while' on the line immediately before free_port call
        # (ignoring comments and blank lines).
        has_guard = False
        for j in range(free_port_call_line - 1, max(-1, free_port_call_line - 6), -1):
            prev = lines[j].strip()
            if not prev or prev.startswith('#'):
                continue
            # The previous non-blank, non-comment line should be an if/while guard
            if prev.startswith('if ') or prev.startswith('while '):
                has_guard = True
            break  # Only check the first non-blank/non-comment line before
        
        assert not free_port_call_line < dashboard_start_line or has_guard, (
            f"BUG: company-start.sh calls free_port() unconditionally at line {free_port_call_line} "
            f"before starting the dashboard at line {dashboard_start_line}. "
            f"This kills any existing dashboard (e.g., started by `hive` no-args). "
            f"It should check if a dashboard is already running first."
        )


class TestHiveStartVsHiveNoArgs:
    """Verify that `hive start` and clicking Start Company behave the same."""

    def test_hive_start_calls_company_start_sh(self):
        """hive start should call company-start.sh (same as dashboard button)."""
        company_py = get_install_dir() / "lib" / "hive_cli" / "commands" / "company.py"
        content = company_py.read_text()
        assert "company-start.sh" in content

    def test_dashboard_start_api_calls_company_start_sh(self):
        """Dashboard /api/company/start should call company-start.sh."""
        server_js = get_install_dir() / "lib" / "dashboard" / "server.js"
        content = server_js.read_text()
        assert "company-start.sh" in content


class TestCompanyStop:
    """Tests for the stop flow."""

    def test_company_stop_sh_kills_daemons(self):
        """company-stop.sh should kill all company processes."""
        cs = get_install_dir() / "lib" / "tools" / "company-stop.sh"
        content = cs.read_text()
        assert "processes.json" in content
        assert "pgrep" in content

    def test_company_stop_does_not_kill_ancestors(self):
        """company-stop.sh must never kill its own process tree."""
        cs = get_install_dir() / "lib" / "tools" / "company-stop.sh"
        content = cs.read_text()
        assert "ANCESTOR_PIDS" in content
        assert "is_ancestor" in content

    def test_hive_stop_calls_company_stop_sh(self):
        """hive stop should call company-stop.sh."""
        company_py = get_install_dir() / "lib" / "hive_cli" / "commands" / "company.py"
        content = company_py.read_text()
        assert "company-stop.sh" in content


class TestMCPServerReuse:
    """Tests for MCP server reuse across startup paths."""

    def test_company_start_sh_reuses_existing_mcp(self):
        """company-start.sh should detect and reuse an existing MCP server."""
        cs = get_install_dir() / "lib" / "tools" / "company-start.sh"
        content = cs.read_text()
        # Should have a check before starting the MCP
        assert "MCP server already running" in content

    def test_mcp_server_check_uses_port(self):
        """The MCP reuse check should verify the port is listening."""
        cs = get_install_dir() / "lib" / "tools" / "company-start.sh"
        content = cs.read_text()
        # Should check via curl or nc on the MCP port
        assert "HIVE_MCP_PORT" in content or "7891" in content
