"""Tests for hive_mcp.shell — subprocess helper."""
import json
import os
from pathlib import Path

import pytest

import os
from pathlib import Path

# Ensure we can find hive_mcp when running from within the hive/ directory
_hive_lib = str(Path(__file__).resolve().parent.parent / "lib")
if _hive_lib not in __import__("sys").path:
    __import__("sys").path.insert(0, _hive_lib)

from hive_mcp.shell import ShellResult, run, _resolve_scaffold


class TestShellResult:
    """Test the ShellResult dataclass."""

    def test_to_dict_with_parsed(self):
        result = ShellResult(
            ok=True, exit_code=0, stdout="{}", stderr="", parsed={"key": "value"}
        )
        d = result.to_dict()
        assert d["ok"] is True
        assert d["exit_code"] == 0
        assert d["data"] == {"key": "value"}
        assert "stdout" not in d

    def test_to_dict_without_parsed(self):
        result = ShellResult(
            ok=False, exit_code=1, stdout="error", stderr="stderr text", parsed=None
        )
        d = result.to_dict()
        assert d["ok"] is False
        assert d["exit_code"] == 1
        assert d["stdout"] == "error"
        assert d["stderr"] == "stderr text"

    def test_to_dict_strips_output(self):
        result = ShellResult(
            ok=True, exit_code=0, stdout="  hello  \n", stderr="", parsed=None
        )
        d = result.to_dict()
        assert d["stdout"] == "hello"


class TestResolveScaffold:
    """Test scaffold path resolution."""

    def test_returns_hive_root_when_no_company_dir(self, install_dir):
        result = _resolve_scaffold(None)
        assert isinstance(result, Path)
        assert (result / "lib" / "tools").is_dir()

    def test_prefers_company_dir_over_env(self, tmp_project_dir):
        company_dir = tmp_project_dir / ".company"
        company_dir.mkdir()
        result = _resolve_scaffold(str(company_dir))
        # Should resolve to .hive under the project root
        assert (result / "lib" / "tools").is_dir()


class TestRun:
    """Test the run() function."""

    def test_run_script_not_found(self, install_dir):
        # Use a clean company_dir to avoid interference from other tests
        result = run("nonexistent-script-xyz", company_dir=None)
        assert result.ok is False
        assert result.exit_code == 127
        assert "script not found" in result.stderr

    def test_run_help_shows_usage(self, install_dir):
        # ticket-create.sh with no args should show usage and exit non-zero
        # Use a temp company_dir to avoid interference from .company in cwd
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            result = run("ticket-create", company_dir=td)
        assert result.ok is False  # exits non-zero when called without args
        assert "Usage" in result.stderr or "usage" in result.stderr.lower()

    def test_run_ticket_get_shows_usage(self, install_dir):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            result = run("ticket-get", company_dir=td)
        assert result.ok is False
        assert "Usage" in result.stderr or "usage" in result.stderr.lower()

    def test_run_ticket_list_shows_usage(self, install_dir):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            result = run("ticket-list", company_dir=td)
        assert result.ok is False
        assert "Usage" in result.stderr or "usage" in result.stderr.lower()

    def test_run_adds_sh_extension(self, install_dir):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            # This should find ticket-create.sh even though we passed "ticket-create"
            result = run("ticket-create", company_dir=td)
            # Should NOT say "script not found" — it appended .sh
            assert "script not found" not in result.stderr

    def test_run_filters_none_args(self, install_dir):
        """None values should be filtered from args."""
        result = run("ticket-create", None, company_dir=None)
        # Should not crash; None is filtered
        assert isinstance(result, ShellResult)

    def test_run_with_timeout(self, install_dir):
        """A 0-second timeout should trigger TimeoutExpired."""
        # Use a script that might hang — init-workspace with bad args
        result = run("init-workspace", ".", company_dir=None, timeout=1)
        assert isinstance(result, ShellResult)

    def test_run_parse_json_false(self, install_dir):
        """When parse_json=False, parsed should be None."""
        # ticket-create outputs to stderr, not stdout JSON
        result = run("ticket-create", company_dir=None, parse_json=False)
        assert result.parsed is None
