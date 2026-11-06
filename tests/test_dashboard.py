"""Tests for hive dashboard — structure and server."""
import json
import re
import shutil
import subprocess
import sys
import os
import time
import urllib.request
import uuid
from pathlib import Path
# Ensure lib/ is on the path when running from within hive/ directory
_hive_lib = str(Path(__file__).resolve().parent.parent / "lib")
if _hive_lib not in sys.path:
    sys.path.insert(0, _hive_lib)



import pytest


DASHBOARD_DIR = "lib/dashboard"
REPO = Path(__file__).resolve().parent.parent
FIXTURE_COMPANY = REPO / "tests" / "fixtures" / "company"


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


# ---------------------------------------------------------------------------
# Static analysis of the DELIVERED SPA (the script the browser actually runs).
#
# Two silent breakages have shipped from this script before, and `node --check`
# cannot see either (scope/lint issues, not syntax errors):
#
#  1. Incomplete feature removal: the meetings/inbox-outbox removal (commit
#     a4ed5c5) deleted the renderMarkdown() helper and the
#     orgShowActiveOnly/toggleOrgActive org-chart state, but the call sites in
#     the ticket detail, chat, reports, brief and org views survived — the
#     views then crashed at runtime with ReferenceError and only the browser
#     console noticed.
#  2. Dead code: a leftover `return html;` after a live return made every
#     browser print "unreachable code after return statement" on load.
#
# The helpers below parse the delivered script the same way a browser would
# (template escapes already evaluated by node when the page is built).
# ---------------------------------------------------------------------------

_SPA_KEYWORDS = {'void', 'delete', 'in', 'else', 'do', 'typeof', 'case', 'return', 'throw', 'of', 'instanceof', 'new'}
def _spa_strip(script):
    out = list(script)
    i, n = 0, len(script)
    pt = ''
    def prev_is_word(t): return bool(t) and (t[0].isalnum() or t[0] in '_$')
    while i < n:
        c = script[i]
        if c == '/' and i+1 < n and script[i+1] == '/':
            j = script.find('\n', i)
            j = n if j < 0 else j
            for k in range(i, j): out[k] = ' '
            i = j; pt = ''
            continue
        if c == '/' and i+1 < n and script[i+1] == '*':
            j = script.find('*/', i+2)
            j = n if j < 0 else j+2
            for k in range(i, j):
                if out[k] != '\n': out[k] = ' '
            i = j; pt = ''
            continue
        if c in ('"', "'"):
            q = c; j = i+1
            while j < n and script[j] != q:
                if script[j] == '\\' and j+1 < n: j += 1
                if script[j] == '\n': break
                j += 1
            j = min(j, n-1)
            for k in range(i, j+1):
                if out[k] != '\n': out[k] = ' '
            i = j+1; pt = 'STR'
            continue
        if c == '`':
            j = i+1
            while j < n and script[j] != '`':
                if script[j] == '\\': j += 1
                j += 1
            j = min(j, n-1)
            for k in range(i, j+1):
                if out[k] != '\n': out[k] = ' '
            i = j+1; pt = 'TPL'
            continue
        if c == '/':
            if prev_is_word(pt):
                is_re = pt in _SPA_KEYWORDS
            elif pt in (')', ']', '.') or pt in ('STR', 'TPL') or (pt and pt[-1].isdigit()):
                is_re = False
            else:
                is_re = True
            if is_re:
                j = i+1; in_class = False
                while j < n and script[j] != '\n':
                    r = script[j]
                    if r == '\\': j += 2; continue
                    if r == '[': in_class = True
                    elif r == ']': in_class = False
                    elif r == '/' and not in_class: break
                    j += 1
                j = min(j, n-1)
                for k in range(i, j+1):
                    if out[k] != '\n': out[k] = ' '
                j += 1
                while j < n and script[j].isalpha(): j += 1
                i = j; pt = 'REGEX'
                continue
            pt = c; i += 1
            continue
        if c.isspace(): i += 1; continue
        if c.isalnum() or c in '_$':
            j = i
            while j < n and (script[j].isalnum() or script[j] in '_$'): j += 1
            pt = script[i:j]; i = j
            continue
        pt = c; i += 1
    return ''.join(out)

_SPA_ALLOWED_GLOBALS = {'URL', 'DOMParser', 'Node', 'async', 'cancelAnimationFrame', 'performance', 'WeakSet', 'Number', 'unescape', 'if', 'Image', 'prompt', 'clearTimeout', 'Object', 'new', 'RangeError', 'import', 'Set', 'with', 'RegExp', 'export', 'atob', 'eval', 'URLSearchParams', 'MutationObserver', 'delete', 'confirm', 'Element', 'function', 'Boolean', 'XMLHttpRequest', 'parseFloat', 'crypto', 'queueMicrotask', 'do', 'alert', 'requestAnimationFrame', 'sessionStorage', 'TypeError', 'window', 'Array', 'yield', 'Worker', 'catch', 'parseInt', 'HTMLElement', 'switch', 'history', 'structuredClone', 'JSON', 'await', 'location', 'getComputedStyle', 'encodeURIComponent', 'of', 'Event', 'setInterval', 'fetch', 'AbortController', 'else', 'Audio', 'assert', 'WeakMap', 'FileReader', 'Promise', 'escape', 'EventSource', 'btoa', 'Error', 'SharedWorker', 'String', 'localStorage', 'clearInterval', 'console', 'IntersectionObserver', 'document', 'typeof', 'while', 'case', 'Blob', 'return', 'throw', 'Map', 'void', 'CustomEvent', 'isNaN', 'setTimeout', 'in', 'navigator', 'decodeURIComponent', 'WebSocket', 'Symbol', 'for', 'Math', 'ResizeObserver', 'Date'}
def _spa_defined_names(stripped):
    defined = set(re.findall(r'(?:async\s+)?function\s+([A-Za-z_$][\w$]*)', stripped))
    for m in re.finditer(r'\b(?:var|let|const)\b', stripped):
        j = m.end()
        while j < len(stripped):
            dm = re.match(r"\s*,?\s*([A-Za-z_$][\w$]*)", stripped[j:])
            if not dm: break
            defined.add(dm.group(1)); j += dm.end()
            nm = re.match(r"\s*=\s*", stripped[j:])
            if not nm: break
            j += nm.end()
    for m in re.finditer(r'function\s*(?:[A-Za-z_$][\w$]*\s*)?\(([^)]*)\)', stripped):
        for p in m.group(1).split(','):
            p = p.strip()
            if re.match(r'^[A-Za-z_$][\w$]*$', p): defined.add(p)
    for m in re.finditer(r'catch\s*\(([^)]*)\)', stripped):
        p = m.group(1).strip()
        if re.match(r'^[A-Za-z_$][\w$]*$', p): defined.add(p)
    return defined

def _spa_undefined_calls(script):
    stripped = _spa_strip(script)
    defined = _spa_defined_names(stripped)
    called = set(re.findall(r'(?<![\w.$])([A-Za-z_$][\w$]*)\s*\(', stripped))
    return sorted(c for c in called - defined - _SPA_ALLOWED_GLOBALS)

def _spa_find_unreachable(script):
    OPS = set('+-*/%<>&|^!~?:,.( [.=>')
    problems = set()
    depth = 0; in_bc = False; prev_open = False
    in_return = False; ret_open = 0
    ret_start_depth = None
    pending_return_depth = None
    for ln, line in enumerate(script.split('\n'), 1):
        s = _spa_strip_line(line, in_bc)
        in_bc = s[1]
        t = s[0].strip()
        if not t:
            continue
        if not prev_open:
            if re.match(r'return\b', t):
                in_return = True; ret_open = 0; ret_start_depth = depth
                if pending_return_depth == depth:
                    problems.add(ln)
            elif not t.startswith('}') and pending_return_depth == depth:
                problems.add(ln)
        if in_return:
            net = t.count('{') - t.count('}')
            if ret_open == 0 and net == 0 and t.endswith((';', '}')):
                pending_return_depth = ret_start_depth
                in_return = False
            ret_open = max(0, ret_open + net)
        for ch in t:
            if ch == '{': depth += 1
            elif ch == '}': depth = max(0, depth - 1)
        if pending_return_depth is not None and depth < pending_return_depth:
            pending_return_depth = None
        bal = 0
        for ch in t:
            if ch in '([{': bal += 1
            elif ch in ')]}': bal -= 1
        prev_open = bal > 0 or (bal == 0 and t and t[-1] in OPS and t[-1] not in ')]};')
    return sorted(problems)

def _spa_strip_line(line, in_block_comment):
    out = []; i, n = 0, len(line)
    while i < n:
        c = line[i]
        if in_block_comment:
            if c == '*' and i+1 < n and line[i+1] == '/':
                in_block_comment = False; i += 2; continue
            i += 1; continue
        if c == '/' and i+1 < n and line[i+1] == '/': break
        if c == '/' and i+1 < n and line[i+1] == '*':
            in_block_comment = True; i += 2; continue
        if c in ('"', "'"):
            q = c; i += 1
            while i < n and line[i] != q:
                if line[i] == '\\' and i+1 < n: i += 1
                i += 1
            i += 1; continue
        out.append(c); i += 1
    return ''.join(out), in_block_comment

def _spa_handler_refs(page, script):
    stripped = _spa_strip(script)
    defined = _spa_defined_names(stripped)
    handlers = set(re.findall(r'on(?:click|change|input|submit|keydown|keyup|blur|focus|mouseenter|mouseleave|mouseover|mouseout|dblclick)="([A-Za-z_$][\w$]*)\s*\(', page))
    kw = {'if','for','while','switch','catch','function','return','typeof','new','delete','void','throw','case','do','else','with','yield','await','async','in','of','this','true','false','null','undefined','var','let','const'}
    return sorted(h for h in handlers - defined - kw)


class TestEmbeddedSpaScript:
    """The SPA lives inside server.js as a JS template literal. CI's
    `node --check lib/dashboard/server.js` parses that literal as a
    string, so it cannot fail on anything the browser would: escapes
    like `\n` are evaluated when the template is built, and a raw
    newline then lands inside a single-quoted JS string of the DELIVERED
    script — the whole SPA fails to parse and the dashboard breaks with
    only a console error ('string literal contains an unescaped line
    break'). This test reproduces exactly what the browser receives and
    syntax-checks it. (The 0.8.1 fix: nine `\n` escapes in the ticket
    move/comment and gate-output code arrived as raw newlines — write
    `\\n` in server.js when the browser should see a newline escape.)"""

    @pytest.fixture(scope="class")
    def delivered_script(self, tmp_path_factory):
        node = shutil.which("node")
        if not node:
            pytest.skip("node unavailable — cannot syntax-check the delivered SPA")
        src = Path(f"{DASHBOARD_DIR}/server.js").read_text()
        start = src.index("const HTML = `")
        i = start + len("const HTML = `")
        raw = []
        while i < len(src):
            c = src[i]
            if c == "\\" and i + 1 < len(src):
                raw.append(c)
                raw.append(src[i + 1])
                i += 2
                continue
            if c == "`":
                break
            raw.append(c)
            i += 1
        # Re-evaluating the same template source in node reproduces the
        # string the server builds and serves (escape processing included).
        work = tmp_path_factory.mktemp("spa-delivered")
        eval_js = work / "html-eval.js"
        eval_js.write_text('module.exports = `' + "".join(raw) + "`;\n")
        delivered = work / "delivered.js"
        dump = subprocess.run(
            [node, "-e",
             "const fs=require('fs');"
             "const html=require(process.argv[1]);"
             "const m=html.match(/<script[^>]*>([\\s\\S]*?)<\\/script>/);"
             "if(!m) throw new Error('no <script> block in the embedded SPA');"
             "fs.writeFileSync(process.argv[2], m[1]);"
             "fs.writeFileSync(process.argv[3], html);",
             str(eval_js), str(delivered), str(work / "page.html")],
            capture_output=True, text=True, timeout=60)
        assert dump.returncode == 0, dump.stderr
        return node, delivered, (work / "page.html").read_text()

    def test_delivered_spa_script_parses(self, delivered_script):
        node, path, _ = delivered_script
        r = subprocess.run([node, "--check", str(path)],
                           capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, (
            "the SPA script the browser RECEIVES fails to parse (server.js "
            "itself is valid, so CI's node --check cannot see this) — look "
            "for string literals that contain raw newlines: template "
            "escapes like \\n are evaluated when the embedded HTML is built, "
            "so write \\\\n in server.js when the browser should see a newline "
            "escape.\n" + r.stderr
        )

    def test_spa_script_calls_only_defined_functions(self, delivered_script):
        """Every function the SPA script calls must be defined in the script.

        A removed feature that leaves call sites behind (see class docstring)
        parses fine but crashes the affected view with
        'ReferenceError: X is not defined' — only visible in the browser
        console."""
        node, path, _ = delivered_script
        missing = _spa_undefined_calls(path.read_text())
        assert not missing, (
            "the delivered SPA script calls functions that are defined "
            "nowhere in the script — the views calling them crash at runtime "
            "with ReferenceError (how the meetings-system removal left "
            "renderMarkdown/renderRosterAgents as dead call sites): %s" % missing
        )

    def test_spa_script_has_no_unreachable_code_after_return(self,
                                                             delivered_script):
        """No statement may follow a completed return in the same block —
        browsers warn 'unreachable code after return statement' on every
        load, which buries real errors in the console."""
        node, path, _ = delivered_script
        script = path.read_text()
        lines = _spa_find_unreachable(script)
        assert not lines, (
            "unreachable code after a return statement in the delivered SPA "
            "script, lines %s:\n%s" % (
                lines,
                "\n".join(script.splitlines()[l - 1].strip() for l in lines))
        )

    def test_spa_event_handlers_resolve_to_defined_functions(self,
                                                             delivered_script):
        """Every function referenced from an on*="fn(...)" attribute in the
        delivered HTML must exist in the SPA script — a removed handler
        breaks the button silently with ReferenceError on click."""
        node, path, html = delivered_script
        missing = _spa_handler_refs(html, path.read_text())
        assert not missing, (
            "the delivered HTML wires on* handlers to functions the SPA "
            "script does not define (ReferenceError on click): %s" % missing
        )

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

    def test_version_matches_pyproject_and_changelog(self):
        """D.1: VERSION, pyproject metadata, and the newest changelog
        entry must all agree — a half-done version bump rots the public
        surface (doctor already flags VERSION/pyproject drift at
        runtime; this pins the source tree itself)."""
        version = Path("VERSION").read_text().strip()
        pyproject = Path("packaging/pyproject.toml").read_text()
        m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.M)
        assert m, "no version field in packaging/pyproject.toml"
        assert m.group(1) == version, \
            f"VERSION={version} but pyproject version={m.group(1)}"
        changelog = Path("CHANGELOG.md").read_text()
        top = re.search(r"^## (\S+)", changelog, re.M)
        assert top, "no version entry in CHANGELOG.md"
        assert top.group(1) == version, \
            f"VERSION={version} but newest changelog entry is {top.group(1)}"


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

    def test_license_is_complete_mit(self):
        """B.1: LICENSE must be the full, usable MIT text.

        The pre-0.7.0 LICENSE was a truncated MIT (324 bytes): the grant
        clause ended mid-sentence and both the conditions block and the
        IN-NO-EVENT liability clause were missing, so as written it was
        not a usable license. All three clause markers must be present.
        """
        # Collapse whitespace so the clause checks don't depend on the
        # exact line wrapping of the license text.
        content = re.sub(r"\s+", " ", Path("LICENSE").read_text())
        assert "Permission is hereby granted, free of charge" in content, \
            "MIT grant clause missing"
        assert ("The above copyright notice and this permission notice "
                "shall be included") in content, \
            "MIT conditions clause (retain the copyright notice) missing"
        assert ("IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE "
                "LIABLE") in content, \
            "MIT IN-NO-EVENT liability clause missing"

    def test_license_names_a_copyright_holder(self):
        """B.1: the copyright line must exist and carry a year + holder."""
        content = Path("LICENSE").read_text()
        assert re.search(r"Copyright \(c\) \d{4} \S+", content), \
            "LICENSE has no 'Copyright (c) <year> <holder>' line"


class TestInstallDoc:
    """Test the INSTALL file."""

    def test_install_exists(self):
        assert Path("INSTALL").exists()

    def test_install_has_content(self):
        content = Path("INSTALL").read_text()
        assert len(content.strip()) > 10


class TestNoStaleVersionStrings:
    """B.2: the public surface must not carry stale 0.5.x version pins.

    INSTALL once shipped a header pinning a release it had long since
    outgrown (and it rots again if pinned). Nothing outside CHANGELOG.md
    — which legitimately records the historical pre-0.6 releases — may
    reference a 0.5.x version: no headers, no doc examples, no test
    fixtures. (The literal is not repeated in this file for the same
    reason test_zero_trace.py assembles its token from parts.)
    """

    @staticmethod
    def _tracked_files():
        r = subprocess.run(["git", "ls-files"], capture_output=True,
                           text=True, timeout=30)
        assert r.returncode == 0, "git ls-files failed"
        return r.stdout.split()

    def test_no_05x_version_outside_changelog(self):
        stale = re.compile(r"0\.5\.\d+")
        offenders = []
        for f in self._tracked_files():
            if f == "CHANGELOG.md":
                continue
            p = Path(f)
            if not p.is_file():
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue  # binary file
            if stale.search(text):
                offenders.append(f)
        assert not offenders, (
            "stale 0.5.x version string outside CHANGELOG.md: "
            f"{offenders}"
        )


class TestContributorDocs:
    """B.3: a public repo needs contributor + security docs."""

    def test_contributing_prescribes_the_dev_loop(self):
        p = Path("CONTRIBUTING.md")
        assert p.exists(), "CONTRIBUTING.md missing"
        content = p.read_text()
        assert "Every change needs a test" in content, \
            "CONTRIBUTING must carry the test-first rule"
        assert "pytest" in content, \
            "CONTRIBUTING must carry the pytest commands"
        assert "Checklist" in content, \
            "CONTRIBUTING must carry the commit checklist"
        assert "AGENTS.md" in content, \
            "CONTRIBUTING must point at the per-directory AGENTS.md map"

    def test_security_demands_private_reporting(self):
        p = Path("SECURITY.md")
        assert p.exists(), "SECURITY.md missing"
        content = p.read_text()
        assert "security/advisories/new" in content, \
            "no private reporting path (security advisory form)"
        low = content.lower()
        assert "do not open a public issue" in low, \
            "must state vulnerabilities are reported privately, never public"
        assert "revoke" in low, \
            "secret-leak response must mention revoking the credential"
        # The leak guidance must forbid pasting live values
        assert "never paste" in low or "by prefix" in low, \
            "must tell reporters not to paste live credential values"

    def test_issue_templates_exist(self):
        for name in ("bug_report.yml", "feature_request.yml"):
            assert (Path(".github/ISSUE_TEMPLATE") / name).exists(), \
                f".github/ISSUE_TEMPLATE/{name} missing"


class TestCILintScope:
    """C.3: the ruff job must cover the full Python surface."""

    WORKFLOW = Path(".github/workflows/ci.yml")

    def test_lint_job_covers_lib_and_tests(self):
        text = self.WORKFLOW.read_text()
        assert "ruff check --select F401,E722 lib/ tests/" in text, \
            "lint job must run ruff over lib/ + tests/ (the full Python " \
            "surface), not just the MCP/CLI packages"

    def test_python_surface_is_ruff_clean(self):
        """Run the CI lint selection locally so an F401/E722 fails here,
        not on the CI runner."""
        if shutil.which("ruff") is None:
            pytest.skip("ruff not installed (pip install -r "
                        "packaging/requirements-dev.txt)")
        result = subprocess.run(
            ["ruff", "check", "--select", "F401,E722", "lib/", "tests/"],
            capture_output=True, text=True, timeout=120,
        )
        assert result.returncode == 0, \
            "ruff check --select F401,E722 lib/ tests/ fails:\n" \
            f"{result.stdout}{result.stderr}"


class TestCISecretScan:
    """C.1: CI must run a permanent secret scan — a secret can never
    land in history again."""

    WORKFLOW = Path(".github/workflows/ci.yml")

    def test_ci_workflow_exists(self):
        assert self.WORKFLOW.exists(), ".github/workflows/ci.yml missing"

    def test_secrets_job_runs_gitleaks_over_full_history(self):
        text = self.WORKFLOW.read_text()
        assert "secrets:" in text, "no 'secrets' job in ci.yml"
        assert "gitleaks detect --source . --redact --no-banner" in text, \
            "the gitleaks detect command (with --redact) is not pinned"
        assert "fetch-depth: 0" in text, \
            "secret scan must see full history, not just the working tree"
        assert re.search(r"GITLEAKS_VERSION=\d+\.\d+\.\d+", text), \
            "gitleaks version must be pinned (a floating version silently " \
            "changes the guard)"


class TestCINodeVersion:
    """C.2: the node-check job must not pin a deprecated Node major."""

    WORKFLOW = Path(".github/workflows/ci.yml")

    def test_node_check_pins_a_current_node(self):
        text = self.WORKFLOW.read_text()
        pinned = re.findall(r"node-version:\s*'?([0-9]+)", text)
        assert pinned, "node-check job does not pin a node version"
        for pin in pinned:
            major = int(pin)
            # Node 20 went EOL April 2026; GitHub runners force-run EOL
            # pins on the current major and annotate the run with a
            # deprecation warning. Don't ship a pin that rots.
            assert major >= 24, \
                f"node-check pins EOL Node {major} — bump to the current major"


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


class TestConfigLlmPanel:
    """C2: the dashboard's Config & LLM panel — which model the agents are
    actually talking to, the last doctor run (pass/fail), and the last
    `hive llm test` result + timestamp. Doctor and llm-test save their
    dumps to .company/state/; the panel reads them (no new daemon). A
    redacted key must never appear in the served payload."""

    @staticmethod
    def _company(tmp_path):
        company = tmp_path / ".company"
        (company / "state").mkdir(parents=True)
        return company

    def test_doctor_saves_dump_to_state(self, tmp_path, monkeypatch):
        import argparse
        self._company(tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        from hive_cli.commands import doctor
        doctor.handle(argparse.Namespace(project_dir=str(tmp_path),
                                         deep=False, json=False))
        dump = json.loads((tmp_path / ".company" / "state" / "doctor.json")
                          .read_text())
        assert dump["ok"] is False  # scratch env: several checks fail
        assert {"name", "ok", "detail"} <= set(dump["checks"][0])
        assert "llm" in dump and "ts" in dump

    def test_llm_test_saves_result_on_dead_endpoint(self, tmp_path, monkeypatch):
        company = self._company(tmp_path)
        monkeypatch.setenv("COMPANY_DIR", str(company))
        monkeypatch.setenv("HIVE_PROVIDER", "openai")
        monkeypatch.setenv("HIVE_MODEL", "m")
        monkeypatch.setenv("HIVE_API_BASE", "http://127.0.0.1:1/v1")
        monkeypatch.setenv("HIVE_API_KEY", "k")
        from hive_cli.commands import llm
        rc = llm.handle(type("A", (), {"action": "test"})())
        assert rc == 1
        data = json.loads((company / "state" / "llm-test.json").read_text())
        assert data["ok"] is False
        assert "127.0.0.1:1" in data["error"]
        assert data["provider"] == "openai"

    def _start_server(self, company, port):
        import subprocess
        proc = subprocess.Popen(
            ["node", "lib/dashboard/server.js", "--dir", str(company),
             "--port", str(port)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return proc

    def _wait(self, port, path="/api/status", timeout=10):
        import time
        import urllib.request
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}{path}", timeout=1):
                    return True
            except Exception:
                time.sleep(0.1)
        return False

    def test_api_config_serves_saved_dumps(self, tmp_path):
        import socket
        import urllib.request
        company = self._company(tmp_path)
        (company / "state" / "doctor.json").write_text(json.dumps({
            "ok": True, "ts": "2026-11-03T00:00:00Z", "version": "0.7.1",
            "checks": [{"name": "LLM provider configured", "ok": True,
                        "detail": "provider=openai model=panel-model "
                                  "base=http://x key=env HIVE_API_KEY"}],
            "warnings": [],
            "llm": {"configured": True, "provider": "openai",
                    "model": "panel-model", "base_url": "http://x",
                    "key_source": "env HIVE_API_KEY"}}))
        (company / "state" / "llm-test.json").write_text(json.dumps({
            "ok": True, "ts": "2026-11-03T00:01:00Z",
            "latency_ms": 12, "reply": "OK", "provider": "openai",
            "model": "panel-model", "base_url": "http://x",
            "key_source": "env HIVE_API_KEY"}))
        s = socket.socket(); s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]; s.close()
        proc = self._start_server(company, port)
        try:
            assert self._wait(port), "dashboard did not start"
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/config", timeout=5) as r:
                data = json.loads(r.read())
            assert data["doctor"]["llm"]["provider"] == "openai"
            assert data["doctor"]["llm"]["model"] == "panel-model"
            assert data["doctor"]["ok"] is True
            assert data["llm_test"]["ok"] is True
            assert data["llm_test"]["reply"] == "OK"
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()

    def test_api_config_never_returns_api_key(self, tmp_path):
        """The redacted key never appears (zero-trace pattern on the
        fixture): even a doctor.json that (improperly) carries an
        api_key field must not serve the value."""
        import socket
        import urllib.request
        company = self._company(tmp_path)
        key = "sk-supersecret-" + "value1234567890abcdef"
        (company / "state" / "doctor.json").write_text(json.dumps({
            "ok": True, "checks": [], "warnings": [],
            "llm": {"provider": "openai", "model": "m",
                    "base_url": "http://x", "key_source": "env HIVE_API_KEY",
                    "api_key": key}}))
        s = socket.socket(); s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]; s.close()
        proc = self._start_server(company, port)
        try:
            assert self._wait(port), "dashboard did not start"
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/config", timeout=5) as r:
                body = r.read().decode()
            assert key not in body, "the key value crossed the wire"
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()

    def test_panel_nav_and_render_markers(self):
        """The Config panel exists in the SPA: nav button wired to
        showConfig(), which fetches /api/config and renders the resolved
        provider + the doctor PASS/FAIL badge."""
        content = Path(f"{DASHBOARD_DIR}/server.js").read_text()
        assert 'onclick="showConfig()">Config</button>' in content
        assert "async function showConfig()" in content
        assert "await api('config')" in content
        assert "cfg-panel-hdr" in content
        assert "key source" in content
        assert "never run" in content


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
    """D.7 (0.6.0: now the AGENTS.md set): the root AGENTS.md
    project-structure block must describe the actual tree. A stale layout
    entry (e.g. a top-level `templates/` that really lives at
    `lib/templates/`) misleads every new contributor and every AI agent
    that reads the docs. These tests parse the fenced structure block and
    assert every listed path exists on disk."""

    DOC_BLOCKS = [("AGENTS.md", "lib")]

    @pytest.mark.parametrize("doc", [
        "AGENTS.md", "lib/AGENTS.md", "lib/core/AGENTS.md",
        "lib/tools/AGENTS.md", "lib/agents/AGENTS.md",
        "lib/hive_mcp/tools/AGENTS.md", "lib/hive_cli/AGENTS.md",
        "lib/hive_cli/commands/AGENTS.md", "lib/dashboard/AGENTS.md",
        "lib/templates/AGENTS.md", "lib/templates/github-workflows/AGENTS.md",
        "lib/templates/publishers/AGENTS.md", "packaging/AGENTS.md",
        "share/docs/AGENTS.md",
    ])
    def test_agents_md_set_present(self, doc):
        """0.6.0 rebrand: every key directory documents itself in AGENTS.md
        and no file using the retired external-CLI doc filename remains.
        (The legacy filename is assembled so this test stays grep-clean.)"""
        assert Path(doc).is_file(), f"{doc} missing"
        # Tracked files only (fast + exact): the repo must not ship any
        # file using the retired external-CLI doc filename.
        legacy_name = "CL" + "AUDE.md"
        r = subprocess.run(["git", "ls-files"], capture_output=True,
                           text=True, timeout=30)
        legacy = [f for f in r.stdout.split() if f.endswith(legacy_name)]
        assert not legacy, f"legacy doc files remain: {legacy}"

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


class TestDockerAgentInABox:
    """A.3: the Dockerfile + entrypoint.sh ship the agent-in-a-box image
    (built-in harness, no external CLI). Build the image (skipif docker is
    unavailable) and assert the isolation model the Dockerfile promises:
    the mounted worktree is visible and writable; .company/ and the rest
    of the host filesystem are NOT visible; /root is a fresh tmpfs; and a
    trivial prompt round-trips against a stubbed LLM endpoint on the
    host's loopback (container runs with --network host).

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
        """Build the agent-in-a-box image once per worker. A failed build is
        a real Dockerfile regression — fail, don't skip."""
        image = f"hive-agent-box-test-{uuid.uuid4().hex[:8]}"
        repo_root = Path(__file__).resolve().parent.parent
        r = subprocess.run(
            ["docker", "build", "-q", "-t", image, "."],
            capture_output=True, text=True, timeout=1800, cwd=str(repo_root),
        )
        if r.returncode != 0:
            pytest.fail(
                "docker build of the agent-in-a-box image failed:"
                f"\n{(r.stderr or r.stdout)[-800:]}"
            )
        yield image
        subprocess.run(["docker", "rmi", "-f", image], capture_output=True)

    @staticmethod
    def _run(hive_image, run_opts=(), *cmd, timeout=300):
        """Run a container the way the Dockerfile documents: worktree rw at
        /work, /root as an ephemeral tmpfs.

        run_opts = `docker run` flags (must precede the image: -v/-w/-e...);
        cmd      = the command executed inside the container (after the
        image — anything after the image name is a command, not a flag)."""
        args = (["docker", "run", "--rm",
                 "--tmpfs", "/root:noexec,nosuid,size=100m"]
                + list(run_opts) + [hive_image] + list(cmd))
        return subprocess.run(args, capture_output=True, text=True,
                              timeout=timeout)

    def test_dockerfile_ships_harness_and_siblings(self):
        """Regression (CI 2026-11-03): the image originally copied ONLY
        agent-harness.py, so its by-name sibling imports (llm_providers,
        mcp_http) silently degraded to None inside the container — env-only
        LLM configs died with a NameError and MCP tool calls were disabled.
        The Dockerfile must copy every lib/core module the harness imports.
        No docker needed — this pins the COPY list itself."""
        dockerfile = (Path(__file__).resolve().parent.parent
                      / "Dockerfile").read_text()
        copied = set(re.findall(
            r"^COPY\s+lib/core/([\w.-]+\.py)\s+/opt/hive/lib/core/",
            dockerfile, re.M))
        for mod in ("agent-harness.py", "llm_providers.py", "mcp_http.py"):
            assert mod in copied, f"Dockerfile does not copy lib/core/{mod}"

    def test_harness_help_smoke(self, hive_image):
        """Default CMD is `agent-harness.py --help` — the harness is installed
        and runs without an API key or network."""
        r = self._run(hive_image)
        assert r.returncode == 0, (r.stderr or r.stdout)[-500:]
        assert (r.stdout + r.stderr).strip(), "harness --help printed nothing"
        assert "usage" in (r.stdout + r.stderr).lower()

    def test_worktree_visible_and_writable(self, hive_image, tmp_path):
        wt = tmp_path / "worktree"
        wt.mkdir()
        (wt / "marker.txt").write_text("worktree-visible")
        r = self._run(hive_image,
                      ["-v", f"{wt}:/work:rw", "-w", "/work"],
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
                      ["-v", f"{wt}:/work:rw", "-w", "/work"],
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

    def test_root_fresh_tmpfs(self, hive_image):
        """/root (agent HOME / state dir) must be a tmpfs: ephemeral, no host
        leakage, nothing carried between runs."""
        r = self._run(hive_image,
                      ["-w", "/root"],
                      "/entrypoint.sh", "bash", "-c",
                      "df -T /root | tail -1 && echo touch-$$ > /root/probe.txt "
                      "&& cat /root/probe.txt")
        assert r.returncode == 0, (r.stderr or r.stdout)[-500:]
        assert "tmpfs" in r.stdout, f"/root is not a tmpfs: {r.stdout[:200]}"

    def test_trivial_prompt_round_trip_against_stub(self, hive_image, tmp_path):
        """Full path: a trivial prompt through the entrypoint + harness,
        answered by a stubbed LLM endpoint on the host's loopback (the
        container runs with --network host). No live provider, no API key:
        the stub returns one canned OpenAI-style completion with no tool
        blocks, so the harness ends the loop and reports the answer."""
        import json as _json
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class _Stub(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                assert self.path.endswith("/chat/completions"), self.path
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                _json.loads(body)  # must be valid JSON
                resp = _json.dumps({
                    "id": "chatcmpl-stub", "object": "chat.completion",
                    "model": "stub-model",
                    "choices": [{"index": 0,
                                 "message": {"role": "assistant", "content": "OK"},
                                 "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1,
                              "total_tokens": 2},
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)

        server = ThreadingHTTPServer(("127.0.0.1", 0), _Stub)
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        try:
            wt = tmp_path / "worktree"
            wt.mkdir()
            r = self._run(hive_image,
                          ["--network", "host",
                           "-e", f"HIVE_API_BASE=http://127.0.0.1:{port}/v1",
                           "-e", "HIVE_API_KEY=stub-key",
                           "-e", "HIVE_MODEL=stub-model",
                           "-v", f"{wt}:/work:rw", "-w", "/work"],
                          "/entrypoint.sh", "Reply with exactly: OK",
                          timeout=300)
        finally:
            server.shutdown()
            server.server_close()
        assert r.returncode == 0, (r.stderr or r.stdout)[-500:]
        assert "OK" in r.stdout, f"stub answer missing from output: {r.stdout[:300]}"


class TestRoundsCostApi:
    """B3 (0.9.0): /api/rounds/history prices each round from the LLM
    usage log (entries whose timestamps fall inside the round's
    started_at/ended_at window) and carries a cumulative `totals`
    block — the 'this sprint' cost across the returned history
    entries. No new state is written; with no usage log the fields
    are 0, not missing.

    Uses the SAME shared fixture company tree (tests/fixtures/company)
    as the bash-side B3 test (test_bash_scripts.TestRoundStatusCost) —
    one fixture tree, two consumers, so a shape rename in the log
    breaks both in the same run (C2's contract)."""

    SERVER = REPO / "lib" / "dashboard" / "server.js"

    @staticmethod
    def _free_port():
        import socket
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        return port

    @staticmethod
    def _get(url, timeout=15):
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=3) as r:
                    return json.loads(r.read().decode())
            except Exception as e:  # server not up yet — keep polling
                last = e
                time.sleep(0.15)
        raise AssertionError(f"dashboard never answered {url}: {last}")

    def _api(self, company, path):
        node = shutil.which("node")
        if not node:
            pytest.skip("node unavailable — cannot run the dashboard server")
        port = self._free_port()
        proc = subprocess.Popen(
            [node, str(self.SERVER), "--dir", str(company), "--port", str(port)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            return self._get(f"http://127.0.0.1:{port}{path}")
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

    def _history(self, company):
        return self._api(company, "/api/rounds/history?limit=20")

    def test_rounds_api_carries_per_round_costs_and_cumulative_total(self):
        j = self._history(FIXTURE_COMPANY)
        by_id = {r["round_id"]: r for r in j["rounds"]}
        assert set(by_id) >= {"round-008", "round-009"}
        r8 = by_id["round-008"]
        assert (r8["calls"], r8["tokens"]) == (2, 300)
        assert r8["cost_usd"] == pytest.approx(1.8e-05)
        r9 = by_id["round-009"]
        assert (r9["calls"], r9["tokens"]) == (3, 1200)
        assert r9["cost_usd"] == pytest.approx(7.2e-05)
        # Cumulative 'this sprint' block across the returned entries —
        # the Nov 3 CEO entry falls outside both windows and is excluded.
        t = j["totals"]
        assert t["calls"] == 5
        assert t["tokens"] == 1500
        assert t["cost_usd"] == pytest.approx(9.0e-05)

    def test_rounds_api_empty_log_fields_present_as_zero(self, tmp_path):
        """Rounds present but no usage log: every round's cost fields
        exist as 0 — the dashboard's rounds panel renders '-'-free.
        (0.8.1/0.8.2 were born exactly from missing-shape crashes.)"""
        company = tmp_path / ".company"
        (company / "rounds" / "history").mkdir(parents=True)
        for f in FIXTURE_COMPANY.glob("rounds/history/*.json"):
            shutil.copy(f, company / "rounds" / "history" / f.name)
        j = self._history(company)
        assert j["rounds"], "expected rounds from the fixture history"
        for r in j["rounds"]:
            assert r["calls"] == 0
            assert r["tokens"] == 0
            assert r["cost_usd"] == 0
        assert j["totals"] == {"calls": 0, "tokens": 0, "cost_usd": 0}

    def test_rounds_api_matches_bash_side_on_same_fixture(self, tmp_path):
        """The dashboard API and round-status.sh --history must price
        the same fixture identically — they are the same feature over
        two surfaces; drift here is the 0.8.x 'two shapes' class."""
        bash = subprocess.run(
            ["bash", str(REPO / "lib" / "tools" / "round-status.sh"), "--history"],
            capture_output=True, text=True, timeout=30,
            env={**os.environ, "COMPANY_DIR": str(FIXTURE_COMPANY)},
            cwd=str(REPO))
        assert bash.returncode == 0, bash.stderr
        b = json.loads(bash.stdout)
        j = self._history(FIXTURE_COMPANY)
        bm = {r["round_id"]: r for r in b["rounds"]}
        jm = {r["round_id"]: r for r in j["rounds"]}
        for rid in ("round-008", "round-009"):
            for key in ("calls", "tokens", "cost_usd"):
                assert bm[rid][key] == pytest.approx(jm[rid][key]), (rid, key)
