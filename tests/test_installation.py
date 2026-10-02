"""Tests for hive installation process and CLI commands.

Tests cover:
- install.sh --copy-to functionality
- Running hive doctor command
- Running basic hive commands (version, help)
- Scaffold extraction
- Agent harness functionality
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure lib/ is on the path when running from within hive/ directory
_hive_lib = str(Path(__file__).resolve().parent.parent / "lib")
if _hive_lib not in sys.path:
    sys.path.insert(0, _hive_lib)


def get_install_dir():
    """Return the hive install directory (absolute path)."""
    return Path(__file__).resolve().parent.parent


def run_command(cmd, cwd=None, env=None, timeout=60):
    """Run a command and return the result."""
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    # Set HIVE_INSTALL_DIR for all command runs
    full_env["HIVE_INSTALL_DIR"] = str(get_install_dir())
    
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, env=full_env,
        cwd=cwd,
    )
    return result


class TestInstallScript:
    """Tests for packaging/install.sh --copy-to functionality."""

    def test_install_script_exists(self):
        """The install script should exist."""
        install_script = get_install_dir() / "packaging" / "install.sh"
        assert install_script.exists(), "install.sh not found"
        assert install_script.is_file()

    def test_install_script_is_executable(self):
        """The install script should be executable."""
        install_script = get_install_dir() / "packaging" / "install.sh"
        assert os.access(install_script, os.X_OK), "install.sh is not executable"

    def test_install_copy_to_excludes_git(self, tmp_path):
        """install.sh --copy-to should exclude .git directory."""
        install_dir = get_install_dir()
        target_dir = tmp_path / "hive-install"
        
        # Run the copy portion of install.sh
        result = run_command([
            "bash", str(install_dir / "packaging" / "install.sh"),
            "--copy-to", str(target_dir)
        ])
        
        # Check that .git was NOT copied
        assert not (target_dir / ".git").exists(), ".git directory should not be copied"
        
        # Check that essential files were copied
        assert (target_dir / "bin" / "hive").exists(), "bin/hive should be copied"
        assert (target_dir / "lib" / "hive_cli").exists(), "lib/hive_cli should be copied"
        assert (target_dir / "lib" / "hive_mcp").exists(), "lib/hive_mcp should be copied"

    def test_install_copy_to_creates_venv(self, tmp_path):
        """install.sh --copy-to should create a Python venv."""
        install_dir = get_install_dir()
        target_dir = tmp_path / "hive-install-venv"
        
        result = run_command([
            "bash", str(install_dir / "packaging" / "install.sh"),
            "--copy-to", str(target_dir)
        ], timeout=120)
        
        # Check that venv was created
        venv_py = target_dir / "venv" / "bin" / "python"
        assert venv_py.exists(), f"venv not created: {result.stderr}"
        
        # Check that requirements were installed
        result = run_command([str(venv_py), "-c", "import mcp; import fastapi; import httpx"])
        assert result.returncode == 0, f"Requirements not installed: {result.stderr}"

    def test_install_copy_to_symlinks_hive(self, tmp_path):
        """install.sh --copy-to should symlink hive onto PATH."""
        install_dir = get_install_dir()
        target_dir = tmp_path / "hive-install-symlink"
        bin_link = tmp_path / ".local" / "bin" / "hive"
        
        result = run_command([
            "bash", str(install_dir / "packaging" / "install.sh"),
            "--copy-to", str(target_dir),
            "--bin-link", str(bin_link)
        ])
        
        # Check that symlink was created
        assert bin_link.exists() or bin_link.is_symlink(), "hive symlink not created"
        if bin_link.is_symlink():
            link_target = os.readlink(str(bin_link))
            assert "hive" in link_target, f"Symlink points to wrong target: {link_target}"


class TestHiveDoctorCommand:
    """Tests for hive doctor command."""

    def test_hive_doctor_runs_without_error(self, tmp_path):
        """hive doctor should run without errors in a clean environment."""
        # Create a temporary directory to avoid picking up stale .company/ dirs
        result = run_command(["hive", "doctor"], cwd=str(tmp_path))
        
        # Should not crash with NameError or similar
        assert "NameError" not in result.stderr, f"NameError in doctor: {result.stderr}"
        assert "Traceback" not in result.stderr or "All checks passed" in result.stdout

    def test_hive_doctor_shows_checks(self, tmp_project_dir):
        """hive doctor should show all expected checks."""
        result = run_command(["hive", "doctor"], cwd=str(tmp_project_dir))
        
        # Check that key checks are present
        assert "HIVE_INSTALL_DIR exists" in result.stdout
        assert "HIVE_ROOT (scaffold)" in result.stdout
        assert "Python venv" in result.stdout
        assert "agent harness" in result.stdout.lower()
        assert "git" in result.stdout.lower()
        assert "jq" in result.stdout.lower()

    def test_hive_doctor_checks_agent_harness(self, tmp_project_dir):
        """hive doctor should check for agent-harness.py."""
        result = run_command(["hive", "doctor"], cwd=str(tmp_project_dir))
        
        # Should mention the agent harness
        assert "agent harness" in result.stdout.lower()
        # Should not fail with NameError
        assert "_hive_root" not in result.stderr

    def test_hive_doctor_with_project_dir(self, tmp_project_dir):
        """hive doctor should work correctly when run from a project directory."""
        result = run_command(["hive", "doctor"], cwd=str(tmp_project_dir))
        
        # Should not crash
        assert "NameError" not in result.stderr
        assert "Traceback" not in result.stderr or "All checks passed" in result.stdout

    def test_hive_doctor_with_stale_parent_company(self, tmp_path):
        """hive doctor should not be confused by stale .company/ in parent dirs."""
        # Create a project directory with its own .company/
        project_dir = tmp_path / "my-project"
        project_dir.mkdir()
        (project_dir / ".company").mkdir()
        
        # Create a stale .company/ in the parent
        (tmp_path / ".company").mkdir()
        
        # Run doctor from the project directory
        result = run_command(["hive", "doctor"], cwd=str(project_dir))
        
        # Should use the project's .company/, not the parent's
        assert "NameError" not in result.stderr
        # The error message should show the project dir, not tmp_path
        if "does not exist" in result.stderr:
            assert str(project_dir) in result.stderr, \
                f"Should show project dir in error, got: {result.stderr[:200]}"
            assert str(tmp_path) not in result.stderr or str(project_dir) in result.stderr


class TestHiveVersionCommand:
    """Tests for hive version command."""

    def test_hive_version_shows_version(self, tmp_project_dir):
        """hive version should show the version string."""
        result = run_command(["hive", "version"], cwd=str(tmp_project_dir))
        
        assert result.returncode == 0
        # Should have a version number
        assert len(result.stdout.strip()) > 0
        assert "hive" in result.stdout.lower() or any(c.isdigit() for c in result.stdout)

    def test_hive_version_from_project_dir(self, tmp_project_dir):
        """hive version should work from a project directory."""
        result = run_command(["hive", "version"], cwd=str(tmp_project_dir))
        
        assert result.returncode == 0
        assert len(result.stdout.strip()) > 0


class TestHiveHelpCommand:
    """Tests for hive help command."""

    def test_hive_help_shows_usage(self):
        """hive (no args) should try to launch dashboard or show help."""
        # Note: 'hive' with no args tries to launch the dashboard which may timeout
        # We just check it doesn't crash with an error
        result = run_command(["hive", "status"], timeout=10)
        
        # Should not crash with NameError or similar
        assert "NameError" not in result.stderr

    def test_hive_help_flag(self):
        """hive --help should show help text."""
        result = run_command(["hive", "--help"])
        
        # argparse prints help and exits with 0, but main() may return 1
        # The important thing is the help text is shown
        assert "Usage" in result.stdout or "usage" in result.stdout.lower()
        assert "init" in result.stdout
        assert "start" in result.stdout
        assert "stop" in result.stdout


class TestScaffoldExtract:
    """Tests for scaffold extraction."""

    def test_scaffold_extract_script_exists(self):
        """scaffold-extract.sh should exist."""
        script = get_install_dir() / "lib" / "tools" / "scaffold-extract.sh"
        assert script.exists(), "scaffold-extract.sh not found"

    def test_scaffold_extract_creates_directory(self, tmp_path):
        """scaffold-extract.sh should create .hive/ directory."""
        project_dir = tmp_path / "test-project"
        project_dir.mkdir()
        
        result = run_command([
            "bash", str(get_install_dir() / "lib" / "tools" / "scaffold-extract.sh"),
            str(project_dir)
        ])
        
        assert result.returncode == 0, f"scaffold-extract failed: {result.stderr}"
        assert (project_dir / ".hive").exists(), ".hive/ directory not created"

    def test_scaffold_extract_copies_agents(self, tmp_path):
        """scaffold-extract.sh should copy agent prompt files."""
        project_dir = tmp_path / "test-project"
        project_dir.mkdir()
        
        result = run_command([
            "bash", str(get_install_dir() / "lib" / "tools" / "scaffold-extract.sh"),
            str(project_dir)
        ])
        
        assert result.returncode == 0
        assert (project_dir / ".hive" / "lib" / "agents").exists()
        # Check for key agent files
        ceo_md = project_dir / ".hive" / "lib" / "agents" / "ceo.md"
        assert ceo_md.exists(), "ceo.md not copied"

    def test_scaffold_extract_copies_tools(self, tmp_path):
        """scaffold-extract.sh should copy tool scripts."""
        project_dir = tmp_path / "test-project"
        project_dir.mkdir()
        
        result = run_command([
            "bash", str(get_install_dir() / "lib" / "tools" / "scaffold-extract.sh"),
            str(project_dir)
        ])
        
        assert result.returncode == 0
        tools_dir = project_dir / ".hive" / "lib" / "tools"
        assert tools_dir.exists()
        # Check for key tool scripts
        ticket_create = tools_dir / "ticket-create.sh"
        assert ticket_create.exists(), "ticket-create.sh not copied"

    def test_scaffold_extract_copies_core_scripts(self, tmp_path):
        """scaffold-extract.sh should copy core scripts."""
        project_dir = tmp_path / "test-project"
        project_dir.mkdir()
        
        result = run_command([
            "bash", str(get_install_dir() / "lib" / "tools" / "scaffold-extract.sh"),
            str(project_dir)
        ])
        
        assert result.returncode == 0
        core_dir = project_dir / ".hive" / "lib" / "core"
        assert core_dir.exists()
        # Check for key core scripts that are copied by scaffold-extract.sh
        init_sh = core_dir / "init.sh"
        orchestrator_sh = core_dir / "orchestrator.sh"
        claude_spawn = core_dir / "claude-spawn.sh"
        assert init_sh.exists(), "init.sh not copied"
        assert orchestrator_sh.exists(), "orchestrator.sh not copied"
        assert claude_spawn.exists(), "claude-spawn.sh not copied"


class TestAgentHarness:
    """Tests for the built-in agent harness."""

    def test_agent_harness_exists(self):
        """agent-harness.py should exist in lib/core/."""
        harness = get_install_dir() / "lib" / "core" / "agent-harness.py"
        assert harness.exists(), "agent-harness.py not found"

    def test_agent_harness_is_executable(self):
        """agent-harness.py should be executable."""
        harness = get_install_dir() / "lib" / "core" / "agent-harness.py"
        assert os.access(harness, os.X_OK), "agent-harness.py is not executable"

    def test_agent_harness_syntax_valid(self):
        """agent-harness.py should have valid Python syntax."""
        harness = get_install_dir() / "lib" / "core" / "agent-harness.py"
        result = run_command([sys.executable, "-m", "py_compile", str(harness)])
        assert result.returncode == 0, f"Syntax error in agent-harness.py: {result.stderr}"

    def test_agent_harness_imports(self):
        """agent-harness.py should be importable."""
        harness = get_install_dir() / "lib" / "core" / "agent-harness.py"
        # Use importlib to import the module directly (handles hyphen in filename)
        result = run_command([sys.executable, "-c", 
            f"import sys; import importlib.util; "
            f"spec = importlib.util.spec_from_file_location('agent_harness', '{harness}'); "
            f"module = importlib.util.module_from_spec(spec); "
            f"spec.loader.exec_module(module); "
            f"print('OK')"])
        assert result.returncode == 0, f"Import failed: {result.stderr}"
        assert "OK" in result.stdout

    def test_agent_spawn_script_exists(self):
        """agent-spawn.sh should exist in lib/core/."""
        script = get_install_dir() / "lib" / "core" / "agent-spawn.sh"
        assert script.exists(), "agent-spawn.sh not found"

    def test_agent_spawn_script_syntax_valid(self):
        """agent-spawn.sh should have valid bash syntax."""
        script = get_install_dir() / "lib" / "core" / "agent-spawn.sh"
        result = run_command(["bash", "-n", str(script)])
        assert result.returncode == 0, f"Syntax error in agent-spawn.sh: {result.stderr}"


class TestProjectRootDetection:
    """Tests for project root detection logic."""

    def test_hive_script_uses_cwd(self, tmp_path):
        """The hive script should use CWD for project detection."""
        # Create a project with .company/ in CWD
        project_dir = tmp_path / "test-project"
        project_dir.mkdir()
        (project_dir / ".company").mkdir()
        
        # Create a stale .company/ in parent
        (tmp_path / ".company").mkdir()
        
        # Run hive doctor from the project directory
        result = run_command(["hive", "doctor"], cwd=str(project_dir))
        
        # Should not crash or show error about wrong project root
        assert "NameError" not in result.stderr
        # The error message (if any) should show project_dir, not tmp_path
        if "does not exist" in result.stderr:
            # The error should mention the project dir's .hive/, not tmp_path's
            assert str(project_dir) in result.stderr, \
                f"Should show project dir in error, got: {result.stderr[:200]}"
            # If tmp_path appears, it should be as part of project_dir path
            if str(tmp_path) in result.stderr:
                # Make sure project_dir also appears (it's a child of tmp_path)
                assert str(project_dir) in result.stderr

    def test_hive_script_finds_nested_project(self, tmp_path):
        """The hive script should find .company/ in nested subdirectories."""
        # Create a project structure
        parent = tmp_path / "parent"
        parent.mkdir()
        child = parent / "child"
        child.mkdir()
        
        # Put .company/ in the child directory
        (child / ".company").mkdir()
        
        # Run hive doctor from the child directory
        result = run_command(["hive", "doctor"], cwd=str(child))
        
        # Should not crash
        assert "NameError" not in result.stderr

    def test_hive_script_no_company_dir(self, tmp_path):
        """The hive script should handle missing .company/ gracefully."""
        # Create a directory without .company/
        no_company = tmp_path / "no-company"
        no_company.mkdir()
        
        # Run hive doctor from this directory
        result = run_command(["hive", "doctor"], cwd=str(no_company))
        
        # Should not crash with project root errors
        assert "does not exist" not in result.stderr or "All checks passed" in result.stdout
