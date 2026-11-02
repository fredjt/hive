"""Tests for hive dashboard — structure and server."""
import re
import shutil
import subprocess
import sys
import os
import uuid
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

    def test_process_table_shows_restart_and_dead_budget(self):
        """B.3: the Running Processes table must surface the supervisor's
        restart_count and budget-dead state (company-health.sh writes both
        into processes.json; the /api/processes endpoint already passes
        them through, so the table is the last missing piece)."""
        content = Path(f"{DASHBOARD_DIR}/server.js").read_text()
        assert "Restarts" in content, "process table needs a Restarts column"
        assert "restart_count" in content
        assert "Dead (budget)" in content, "budget-dead state needs a distinct badge"


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


class TestDevWorkflowDocs:
    """D.11: the dev workflow docs must keep documenting the fast loop.

    The suite is xdist-safe and parallel is the fast path (~65s -> ~15s);
    the watch loop (ptw) is the long-session path. If either document
    silently disappears, developers regress to the slow serial loop."""

    def test_dev_requirements_declare_test_tools(self):
        """Dev/test deps live in packaging/requirements-dev.txt and include
        the parallel + watch tooling referenced by AGENTS.md."""
        p = Path("packaging/requirements-dev.txt")
        assert p.exists(), "packaging/requirements-dev.txt missing"
        content = p.read_text()
        assert "pytest-xdist" in content, "parallel runner (pytest-xdist) not declared"
        assert "pytest-watch" in content, "watch loop (pytest-watch) not declared"

    def test_agents_md_documents_parallel_suite(self):
        """AGENTS.md must show the parallel command, not just serial."""
        content = Path("AGENTS.md").read_text()
        assert "-n 8" in content, "AGENTS.md does not document the parallel suite (-n 8)"
        assert "pytest-xdist" in content

    def test_agents_md_documents_watch_loop(self):
        """AGENTS.md must document the ptw watch loop (command + usage)."""
        content = Path("AGENTS.md").read_text()
        assert "ptw" in content, "watch loop (ptw) not documented in AGENTS.md"
        assert "--watch lib/" in content


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


class TestLayoutDocsMatchTree:
    """D.7: the AGENTS.md / CLAUDE.md project-structure blocks must describe
    the actual tree. A stale layout entry (e.g. a top-level `templates/`
    that really lives at `lib/templates/`) misleads every new contributor
    and every AI agent that reads the docs. These tests parse the fenced
    structure block and assert every listed path exists on disk."""

    DOC_BLOCKS = [("AGENTS.md", "lib"), ("CLAUDE.md", "lib")]

    @staticmethod
    def _structure_block(doc: str) -> list[str]:
        text = Path(doc).read_text()
        # Find fenced code blocks; the structure block contains bin/hive
        for m in re.finditer(r"```[a-zA-Z]*\n(.*?)```", text, re.DOTALL):
            if "bin/hive" in m.group(1):
                return m.group(1).splitlines()
        raise AssertionError(f"no structure block (with bin/hive) found in {doc}")

    @classmethod
    def _entries(cls, doc: str) -> list[tuple[str, str]]:
        """Parse the indented structure block into (indent, name) pairs.
        Indent 0 = top-level, indent 2 = under lib/, indent 4 = nested."""
        out = []
        for line in cls._structure_block(doc):
            m = re.match(r"^(\s*)(\S+)\s{2,}\S", line)
            if not m:
                continue
            out.append((len(m.group(1)), m.group(2)))
        return out

    @pytest.mark.parametrize("doc", [d for d, _ in DOC_BLOCKS])
    def test_structure_block_lists_only_real_paths(self, doc):
        """Every path listed in the structure block must exist in the tree.
        Catches stale layout docs where a listed directory has moved or
        been renamed (e.g. top-level templates/ vs lib/templates/)."""
        entries = self._entries(doc)
        assert entries, f"structure block in {doc} parsed to no entries"
        # Track the current top-level dir to resolve relative sub-paths
        top = ""
        missing = []
        for indent, name in entries:
            if indent == 0:
                top = name
            # Build the full relative path from the indent stack
            if indent == 0:
                full = name
            elif indent == 2:
                full = f"{top[:-1]}/{name}" if top.endswith("/") else f"{top}/{name}"
            else:
                # Nested deeper; check the name exists somewhere reasonable.
                # We only strictly validate indent-0 and indent-2 paths.
                continue
            if not Path(full).exists():
                missing.append(full)
        assert not missing, (
            f"{doc} structure block lists paths that do not exist: {missing}"
        )

    def test_no_stale_top_level_templates(self):
        """The specific D.7 regression: templates live at lib/templates/,
        NOT at a top-level templates/. Assert the docs do not list a
        top-level templates/ (indent 0), and that lib/templates/ is real."""
        assert Path("lib/templates").is_dir(), "lib/templates/ must exist"
        assert not Path("templates").is_dir(), "a top-level templates/ must NOT exist"
        for doc, _ in self.DOC_BLOCKS:
            entries = self._entries(doc)
            top_level_templates = [
                n for i, n in entries if i == 0 and n.rstrip("/") == "templates"
            ]
            assert not top_level_templates, (
                f"{doc} lists a top-level 'templates/' — the real path is lib/templates/"
            )


class TestGitignoreBuildLitter:
    """D.7: a normal build-and-test cycle must not leave untracked noise.
    build-package.sh → dist/, coverage → .coverage, pytest's basetemp
    (redirected by TMPDIR) → unsloth-tmp/, and pytest's own cache dir.
    All must be gitignored so `git status` stays clean after a dev session."""

    REQUIRED_IGNORES = ["dist/", ".coverage", "unsloth-tmp/", ".pytest_cache/"]

    def test_gitignore_covers_build_and_test_litter(self):
        gi = Path(".gitignore")
        assert gi.is_file(), ".gitignore must exist"
        text = gi.read_text()
        # Collect the ignore patterns (strip comments/blank lines, trim)
        patterns = set()
        for line in text.splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            patterns.add(s)
            # A bare "dist" ignores both file and dir; normalize trailing slash
            patterns.add(s.rstrip("/"))
        missing = [p for p in self.REQUIRED_IGNORES
                   if p not in patterns and p.rstrip("/") not in patterns]
        assert not missing, (
            f".gitignore does not cover build/test litter: {missing}"
        )


class TestDockerClaudeInABox:
    """D.8: the Dockerfile + entrypoint.sh ship the claude-in-a-box image
    with zero coverage. Build the image (skipif docker is unavailable) and
    assert the isolation model the Dockerfile promises: the mounted worktree
    is visible and writable; .company/ and the rest of the host filesystem
    are NOT visible; /root/.claude is a fresh tmpfs seeded from the ro
    /host-claude mount (stale cache not copied); and a trivial prompt runs.

    Runs for real wherever the docker daemon is reachable (CI); skipped
    locally when only the CLI is present or the socket is not accessible."""

    @pytest.fixture(scope="module")
    def docker_cli(self):
        if shutil.which("docker") is None:
            pytest.skip("docker CLI not available")
        r = subprocess.run(["docker", "info"], capture_output=True, text=True,
                           timeout=30)
        if r.returncode != 0:
            pytest.skip(
                "docker daemon not reachable "
                f"({r.stderr.strip().splitlines()[-1] if r.stderr.strip() else 'rc=%d' % r.returncode})"
            )
        yield "docker"

    @pytest.fixture(scope="module")
    def hive_image(self, docker_cli):
        """Build the claude-in-a-box image once per worker. A failed build is
        a real Dockerfile regression — fail, don't skip."""
        image = f"hive-claude-box-test-{uuid.uuid4().hex[:8]}"
        repo_root = Path(__file__).resolve().parent.parent
        r = subprocess.run(
            ["docker", "build", "-q", "-t", image, "."],
            capture_output=True, text=True, timeout=1800, cwd=str(repo_root),
        )
        if r.returncode != 0:
            pytest.fail(
                "docker build of the claude-in-a-box image failed:"
                f"\n{(r.stderr or r.stdout)[-800:]}"
            )
        yield image
        subprocess.run(["docker", "rmi", "-f", image], capture_output=True)

    @staticmethod
    def _run(hive_image, *args, timeout=300):
        """Run a container the way the Dockerfile documents: worktree rw at
        /work, /root/.claude as an ephemeral tmpfs."""
        cmd = (["docker", "run", "--rm",
                "--tmpfs", "/root/.claude:noexec,nosuid,size=100m",
                hive_image] + list(args))
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout)

    def test_claude_binary_smoke(self, hive_image):
        """Default CMD is `claude --help` — the binary is installed and runs
        without an API key or network."""
        r = self._run(hive_image)
        assert r.returncode == 0, (r.stderr or r.stdout)[-500:]
        assert (r.stdout + r.stderr).strip(), "claude --help printed nothing"

    def test_worktree_visible_and_writable(self, hive_image, tmp_path):
        wt = tmp_path / "worktree"
        wt.mkdir()
        (wt / "marker.txt").write_text("worktree-visible")
        host_claude = tmp_path / "host-claude"
        host_claude.mkdir()
        r = self._run(hive_image,
                      "-v", f"{wt}:/work:rw", "-w", "/work",
                      "-v", f"{host_claude}:/host-claude:ro",
                      "/entrypoint.sh", "bash", "-c",
                      "cat /work/marker.txt && echo from-container > /work/created.txt"
                      " && cat /work/created.txt")
        assert r.returncode == 0, (r.stderr or r.stdout)[-500:]
        assert "worktree-visible" in r.stdout
        assert "from-container" in r.stdout
        # The write landed back on the host worktree (rw mount).
        assert (wt / "created.txt").is_file()

    def test_company_and_host_fs_not_visible(self, hive_image, tmp_path):
        """The core promise: the container sees ONLY the worktree. A secret in
        the host's .company/ and an unmounted host file must be unreachable.
        Unique markers make false positives impossible."""
        wt = tmp_path / "worktree"
        wt.mkdir()
        (wt / "visible.txt").write_text("ok")

        company = tmp_path / ".company"
        (company / "secrets").mkdir(parents=True)
        secret_marker = f"hive-company-secret-{uuid.uuid4().hex}"
        (company / "secrets" / "state.json").write_text(secret_marker)

        host_marker = f"hive-host-file-{uuid.uuid4().hex}"
        (tmp_path / host_marker).write_text("host fs file")

        r = self._run(hive_image,
                      "-v", f"{wt}:/work:rw", "-w", "/work",
                      "/entrypoint.sh", "bash", "-c",
                      "echo SECRET_COUNT=$(find / -xdev -name {secret_marker} 2>/dev/null | wc -l)"
                      " && echo HOST_COUNT=$(find / -xdev -name {host_marker} 2>/dev/null | wc -l)"
                      " && test ! -e /.company && test ! -e /work/.company"
                      " && echo NO_COMPANY_DIR".format(
                          secret_marker=secret_marker, host_marker=host_marker))
        assert r.returncode == 0, (r.stderr or r.stdout)[-500:]
        out = r.stdout
        assert "SECRET_COUNT=0" in out, (
            f"host .company secret is reachable inside the container: {out[:300]}")
        assert "HOST_COUNT=0" in out, (
            f"unmounted host file is reachable inside the container: {out[:300]}")
        assert "NO_COMPANY_DIR" in out
        assert secret_marker not in out
        assert host_marker not in out

    def test_root_claude_fresh_tmpfs_seeded_from_host_mount(self, hive_image,
                                                            tmp_path):
        """entrypoint.sh: config from the ro /host-claude mount is copied into
        the tmpfs; cache/ is NOT copied (fresh tmpfs wins); /root/.claude is
        actually a tmpfs."""
        wt = tmp_path / "worktree"
        wt.mkdir()
        host_claude = tmp_path / "host-claude"
        (host_claude / "cache").mkdir(parents=True)
        stale = f"stale-cache-{uuid.uuid4().hex}"
        (host_claude / "cache" / stale).write_text("stale cache entry")
        cfg_marker = f"hive-cfg-{uuid.uuid4().hex}"
        (host_claude / "settings.json").write_text(cfg_marker)

        r = self._run(hive_image,
                      "-v", f"{wt}:/work:rw", "-w", "/work",
                      "-v", f"{host_claude}:/host-claude:ro",
                      "/entrypoint.sh", "bash", "-c",
                      f"cat /root/.claude/settings.json; "
                      f"test ! -e /root/.claude/cache/{stale} && echo NO_STALE_CACHE; "
                      "test -d /root/.claude/cache && echo CACHE_DIR_FRESH; "
                      "df -T /root/.claude | tail -1")
        assert r.returncode == 0, (r.stderr or r.stdout)[-500:]
        assert cfg_marker in r.stdout, "host settings.json was not seeded into /root/.claude"
        assert "NO_STALE_CACHE" in r.stdout, "stale host cache leaked into the tmpfs"
        assert "CACHE_DIR_FRESH" in r.stdout
        assert "tmpfs" in r.stdout, "/root/.claude is not a tmpfs"

    def test_trivial_prompt(self, hive_image, tmp_path):
        """Full path: a real (trivial) prompt through the entrypoint. Needs an
        API key — skip when not available."""
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            pytest.skip("ANTHROPIC_API_KEY not set — cannot run a live prompt")
        wt = tmp_path / "worktree"
        wt.mkdir()
        r = self._run(hive_image,
                      "-e", "ANTHROPIC_API_KEY",
                      "-v", f"{wt}:/work:rw", "-w", "/work",
                      "/entrypoint.sh", "claude", "-p",
                      "Reply with exactly: OK",
                      timeout=600)
        assert r.returncode == 0, (r.stderr or r.stdout)[-500:]
        assert r.stdout.strip(), "claude -p produced no output"
