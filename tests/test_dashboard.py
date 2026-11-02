"""Tests for hive dashboard — structure and server."""
import sys
import os
from pathlib import Path
# Ensure lib/ is on the path when running from within hive/ directory
_hive_lib = str(Path(__file__).resolve().parent.parent / "lib")
if _hive_lib not in sys.path:
    sys.path.insert(0, _hive_lib)



import pytest


DASHBOARD_DIR = "lib/dashboard"


class TestDashboardFiles:
    """Test that dashboard files exist and are valid."""

    def test_dashboard_server_exists(self):
        assert Path(f"{DASHBOARD_DIR}/server.js").exists()

    def test_dashboard_server_is_not_empty(self):
        content = Path(f"{DASHBOARD_DIR}/server.js").read_text()
        assert len(content) > 1000, "Dashboard server.js is suspiciously small"

    def test_dashboard_package_json_exists(self):
        assert Path(f"{DASHBOARD_DIR}/package.json").exists()

    def test_dashboard_package_json_is_valid(self):
        import json
        pkg = json.loads(Path(f"{DASHBOARD_DIR}/package.json").read_text())
        assert "name" in pkg


class TestDashboardServerJS:
    """Test dashboard server.js structure."""

    def test_server_js_parses(self):
        """server.js should be valid JavaScript (basic check)."""
        content = Path(f"{DASHBOARD_DIR}/server.js").read_text()
        # Check for key patterns that indicate a valid Express-like server
        assert "http" in content.lower() or "require" in content
        assert "port" in content.lower()

    def test_server_js_has_mcp_client(self):
        """server.js should reference MCP."""
        content = Path(f"{DASHBOARD_DIR}/server.js").read_text()
        assert "mcp" in content.lower() or "MCP" in content


class TestDashboardTemplates:
    """Test dashboard HTML templates if any exist."""

    def test_gate_output_view_highlights_skips(self):
        """A skipped gate (security-clean with no audit tool) must be
        visually distinct from a pass in the gate output view - a silent
        skip reads exactly like a pass otherwise."""
        content = Path(f"{DASHBOARD_DIR}/server.js").read_text()
        assert "SKIPPED" in content, "gate output view has no skip banner"
        assert "not scanned" in content, (
            "skip banner does not say the gate was not scanned"
        )

    def test_gate_output_view_highlights_each_line(self):
        """The gate log is parsed line-by-line (PASS/SKIP/FAIL/BOUNCE
        get distinct colors) instead of one uncolored <pre>."""
        content = Path(f"{DASHBOARD_DIR}/server.js").read_text()
        assert "display:block" in content, (
            "gate output lines are not rendered as highlighted blocks"
        )
        # The four outcome colors used by the gate log parser.
        for color in ("#7ee787", "#f0b429", "#ff7b72", "#c9d1d9"):
            assert color in content

    def test_dashboard_dir_structure(self):
        """Dashboard directory should have expected structure."""
        assert Path(DASHBOARD_DIR).is_dir()


class TestTemplatesDir:
    """Test the templates directory."""

    def test_templates_dir_exists(self):
        assert Path("lib/templates").is_dir()

    def test_publishers_dir_exists(self):
        assert Path("lib/templates/publishers").is_dir()

    def test_github_workflows_dir_exists(self):
        assert Path("lib/templates/github-workflows").is_dir()

    def test_github_workflows_has_files(self):
        wf_dir = Path("lib/templates/github-workflows")
        files = list(wf_dir.glob("*"))
        assert len(files) > 0, "github-workflows directory is empty"


class TestPackagingDir:
    """Test the packaging directory."""

    def test_packaging_dir_exists(self):
        assert Path("packaging").is_dir()

    def test_pyproject_toml_exists(self):
        assert Path("packaging/pyproject.toml").exists()

    def test_install_script_exists(self):
        install_script = Path("packaging/install.sh")
        if install_script.exists():
            assert os.access(str(install_script), os.X_OK)


class TestShareDir:
    """Test the share directory."""

    def test_share_dir_exists(self):
        assert Path("share").is_dir()

    def test_docs_dir_exists(self):
        assert Path("share/docs").is_dir()

    def test_docs_have_files(self):
        docs = Path("share/docs")
        files = list(docs.glob("*"))
        assert len(files) > 0, "share/docs directory is empty"


class TestEtcDir:
    """Test the etc directory."""

    def test_config_defaults_exists(self):
        assert Path("etc/config.defaults.toml").exists()


class TestBinDir:
    """Test the bin directory."""

    def test_hive_binary_exists(self):
        assert Path("bin/hive").exists()

    def test_hive_binary_is_script(self):
        content = Path("bin/hive").read_text()
        assert content.startswith("#!/") or content.startswith("#!")


class TestLibDirStructure:
    """Test the lib directory structure."""

    def test_lib_agents_exists(self):
        assert Path("lib/agents").is_dir()

    def test_lib_core_exists(self):
        assert Path("lib/core").is_dir()

    def test_lib_dashboard_exists(self):
        assert Path("lib/dashboard").is_dir()

    def test_lib_hive_cli_exists(self):
        assert Path("lib/hive_cli").is_dir()

    def test_lib_hive_mcp_exists(self):
        assert Path("lib/hive_mcp").is_dir()

    def test_lib_tools_exists(self):
        assert Path("lib/tools").is_dir()


class TestPythonPackageStructure:
    """Test Python package __init__.py files."""

    def test_hive_cli_init_exists(self):
        assert Path("lib/hive_cli/__init__.py").exists()

    def test_hive_mcp_init_exists(self):
        assert Path("lib/hive_mcp/__init__.py").exists()

    def test_hive_cli_commands_init_exists(self):
        assert Path("lib/hive_cli/commands/__init__.py").exists()

    def test_hive_mcp_tools_init_exists(self):
        assert Path("lib/hive_mcp/tools/__init__.py").exists()


class TestMainModule:
    """Test the main module."""

    def test_main_py_exists(self):
        assert Path("lib/hive_cli/main.py").exists()

    def test_main_py_has_main_function(self):
        content = Path("lib/hive_cli/main.py").read_text()
        assert "def main" in content

    def test_main_py_has_parser(self):
        content = Path("lib/hive_cli/main.py").read_text()
        assert "build_parser" in content or "ArgumentParser" in content


class TestMCPServerModule:
    """Test the MCP server module."""

    def test_server_py_exists(self):
        assert Path("lib/hive_mcp/server.py").exists()

    def test_server_py_has_build_server(self):
        content = Path("lib/hive_mcp/server.py").read_text()
        assert "def build_server" in content

    def test_server_py_has_main(self):
        content = Path("lib/hive_mcp/server.py").read_text()
        assert "def main" in content


class TestShellModule:
    """Test the shell module."""

    def test_shell_py_exists(self):
        assert Path("lib/hive_mcp/shell.py").exists()

    def test_shell_py_has_run_function(self):
        content = Path("lib/hive_mcp/shell.py").read_text()
        assert "def run" in content

    def test_shell_py_has_shell_result(self):
        content = Path("lib/hive_mcp/shell.py").read_text()
        assert "ShellResult" in content


class TestMCPClientModule:
    """Test the MCP client module."""

    def test_mcp_client_py_exists(self):
        assert Path("lib/hive_cli/mcp_client.py").exists()

    def test_mcp_client_has_call_tool(self):
        content = Path("lib/hive_cli/mcp_client.py").read_text()
        assert "def call_tool" in content


class TestVersionFile:
    """Test the VERSION file."""

    def test_version_file_exists(self):
        assert Path("VERSION").exists()

    def test_version_file_content(self):
        content = Path("VERSION").read_text().strip()
        # Should be a version string
        assert len(content) > 0


class TestREADME:
    """Test the README."""

    def test_readme_exists(self):
        assert Path("README.md").exists()

    def test_readme_mentions_key_components(self):
        content = Path("README.md").read_text()
        # Should mention key concepts
        mentions = ["agent", "ticket", "sprint", "verification"]
        found = [m for m in mentions if m.lower() in content.lower()]
        assert len(found) >= 2, f"README should mention more components. Found: {found}"


class TestCLIDocs:
    """Test CLI documentation."""

    def test_cli_docs_exist(self):
        docs = Path("share/docs")
        cli_doc = docs / "CLI.md"
        assert cli_doc.exists(), "share/docs/CLI.md not found"

    def test_architecture_docs_exist(self):
        docs = Path("share/docs")
        arch_doc = docs / "ARCHITECTURE.md"
        assert arch_doc.exists(), "share/docs/ARCHITECTURE.md not found"


class TestLicense:
    """Test the LICENSE file."""

    def test_license_exists(self):
        assert Path("LICENSE").exists()

    def test_license_has_content(self):
        content = Path("LICENSE").read_text()
        assert len(content.strip()) > 10


class TestInstallDoc:
    """Test the INSTALL file."""

    def test_install_exists(self):
        assert Path("INSTALL").exists()

    def test_install_has_content(self):
        content = Path("INSTALL").read_text()
        assert len(content.strip()) > 10


class TestPullRequestTemplate:
    """Test the PR template."""

    def test_pr_template_exists(self):
        assert Path("lib/templates/PULL_REQUEST_TEMPLATE.md").exists()


import json as _json
import socket
import subprocess
import time
import urllib.error
import urllib.request


def _node_available():
    try:
        subprocess.run(["node", "--version"], capture_output=True, timeout=10)
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _node_available(), reason="node not available")
class TestDashboardServerRobustness:
    """Boot the real dashboard server and probe its robustness."""

    @staticmethod
    def _free_port():
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        p = s.getsockname()[1]
        s.close()
        return p

    def test_server_survives_malformed_json_body(self):
        """A malformed JSON body on any API endpoint must not crash the
        server.

        Regression: handleApi is an async function returned from the request
        handler with no .catch, so a rejected readBody() (malformed JSON)
        became an unhandled promise rejection — Node >= 15 terminates the
        process on unhandled rejections, so one malformed request killed the
        entire dashboard.
        """
        port = self._free_port()
        proc = subprocess.Popen(
            ["node", "lib/dashboard/server.js", "--port", str(port)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            up = False
            deadline = time.time() + 10
            while time.time() < deadline:
                if proc.poll() is not None:
                    raise AssertionError("dashboard process exited before becoming ready")
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=1):
                        up = True
                    break
                except Exception:
                    time.sleep(0.1)
            assert up, "dashboard did not start"

            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/connect",
                data=b"{not valid json",
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            try:
                with urllib.request.urlopen(req, timeout=5) as r:
                    r.read()
            except urllib.error.HTTPError as e:
                assert 400 <= e.code < 500, (
                    f"malformed body should yield a 4xx client error, got {e.code}"
                )
            except Exception:
                # Connection reset — the pre-fix crash behavior. The liveness
                # check below is what makes the test fail.
                pass

            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=5) as r:
                data = _json.loads(r.read())
            assert "connected" in data
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

    def test_gate_view_passes_dept_context(self):
        """B.9: per-ticket gate buttons must carry dept context - the gate
        report lives under departments/<dept>/components/<ticket>/, so a
        ticket id alone can address the wrong dept's report (or 404)."""
        content = Path(f"{DASHBOARD_DIR}/server.js").read_text()
        assert "async function openGateOutput(dept,ticket)" in content
        assert "gates/'+encodeURIComponent(dept)" in content
        assert "+encodeURIComponent(ticket)" in content
        assert 'class="vbtn-watch" data-dept="' in content
        assert 'data-ticket="' in content
        assert "runGates(b.dataset.dept,b.dataset.ticket)" in content
        assert "openGateOutput(b.dataset.dept,b.dataset.ticket)" in content


class TestTicketBoardWritePath:
    """C.10: ticket-board write path — fields typed into the create form
    must actually be persisted, and write failures must surface to the
    user instead of being swallowed."""

    def _content(self):
        return Path(f"{DASHBOARD_DIR}/server.js").read_text()

    def test_create_endpoint_forwards_labels_and_ac(self):
        """The create form collects labels + acceptance criteria; the POST
        handler used to drop both, so every dashboard ticket was created
        with labels==[] and acceptance_criteria==[] (silent data loss).
        It must forward them to MCP ticket_create."""
        content = self._content()
        i = content.find("mcpCall('ticket_create'")
        assert i >= 0, "POST /tickets no longer proxies to MCP ticket_create"
        seg = content[i:i + 1000]
        assert "labels:" in seg, "create endpoint does not forward labels"
        assert "acceptance_criteria:" in seg, (
            "create endpoint does not forward acceptance_criteria"
        )

    def test_ticket_modal_renders_labels(self):
        """Labels are stored on the ticket — the detail modal must show
        them, otherwise the field is write-only and invisible."""
        content = self._content()
        assert "df('Labels'" in content

    def test_move_ticket_surfaces_failure(self):
        """A rejected move (done hard-gate, role gate, MCP down) used to be
        silent: the modal closed and the board re-rendered with the ticket
        still in its old column and no explanation. The PATCH response must
        be checked and the failure alerted."""
        content = self._content()
        i = content.find("async function moveTicket")
        assert i >= 0
        seg = content[i:i + 800]
        assert "r=await api(" in seg, "moveTicket ignores the PATCH response"
        assert "r&&r.error" in seg, "moveTicket does not check r.error"
        assert "Move failed" in seg

    def test_add_comment_surfaces_failure(self):
        content = self._content()
        i = content.find("async function addComment")
        assert i >= 0
        seg = content[i:i + 800]
        assert "r=await api(" in seg, "addComment ignores the PATCH response"
        assert "r&&r.error" in seg, "addComment does not check r.error"
        assert "Comment failed" in seg

    def test_show_ticket_handles_missing_ticket(self):
        """404 {error:'Ticket not found'} used to render a modal full of
        'undefined' fields; the response must be checked."""
        content = self._content()
        i = content.find("async function showTicket")
        assert i >= 0
        seg = content[i:i + 900]
        assert "t&&t.error" in seg, "showTicket does not check the error response"

    def test_show_board_surfaces_fetch_failure(self):
        """A failed first fetch used to reject with no feedback, leaving the
        view on the previous panel's DOM. showBoard must catch and render
        the error."""
        content = self._content()
        i = content.find("async function showBoard")
        assert i >= 0
        seg = content[i:i + 1000]
        assert "try {" in seg and "catch(e)" in seg, (
            "showBoard does not catch the first-fetch failure"
        )
        assert "Failed to load tickets" in seg
