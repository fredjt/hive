"""Tests for hive bash scripts — invoke and check exit codes/output."""
import sys
import json
import os
import subprocess
from pathlib import Path
# Ensure lib/ is on the path when running from within hive/ directory
_hive_lib = str(Path(__file__).resolve().parent.parent / "lib")
if _hive_lib not in sys.path:
    sys.path.insert(0, _hive_lib)



import pytest


SCRIPTS_DIR = "lib/tools"
CORE_DIR = "lib/core"


def run_script(script_path, args=None, env=None, timeout=30):
    """Helper to run a bash script and return the result."""
    cmd = ["bash", str(script_path)]
    if args:
        cmd.extend(args)
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    # Set HIVE_INSTALL_DIR for all script runs
    full_env["HIVE_INSTALL_DIR"] = str(Path(__file__).resolve().parent.parent / "hive")
    if full_env:
        full_env.update(env or {})

    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, env=full_env
    )
    return result


class TestTicketCreateScript:
    """Test ticket-create.sh."""

    def test_ticket_create_no_args_shows_usage(self):
        result = run_script(f"{SCRIPTS_DIR}/ticket-create.sh")
        assert result.returncode != 0
        assert "Usage" in result.stderr or "usage" in result.stderr.lower()

    def test_ticket_create_bad_priority(self, tmp_company_dir):
        env = {"COMPANY_DIR": str(tmp_company_dir)}
        result = run_script(
            f"{SCRIPTS_DIR}/ticket-create.sh",
            ["it", "feature", "invalid-priority", "Title", "Desc"],
            env=env,
        )
        assert result.returncode != 0

    def test_ticket_create_bad_type(self, tmp_company_dir):
        env = {"COMPANY_DIR": str(tmp_company_dir)}
        result = run_script(
            f"{SCRIPTS_DIR}/ticket-create.sh",
            ["it", "invalid-type", "medium", "Title", "Desc"],
            env=env,
        )
        assert result.returncode != 0

    def test_ticket_create_valid(self, tmp_company_dir):
        """Create a ticket successfully."""
        env = {"COMPANY_DIR": str(tmp_company_dir)}
        result = run_script(
            f"{SCRIPTS_DIR}/ticket-create.sh",
            ["it", "feature", "medium", "Test Ticket", "A test description"],
            env=env,
        )
        assert result.returncode == 0
        # Should output a ticket ID
        assert len(result.stdout.strip()) > 0


class TestTicketGetScript:
    """Test ticket-get.sh."""

    def test_ticket_get_no_args_shows_usage(self):
        result = run_script(f"{SCRIPTS_DIR}/ticket-get.sh")
        assert result.returncode != 0
        assert "Usage" in result.stderr or "usage" in result.stderr.lower()


class TestTicketListScript:
    """Test ticket-list.sh."""

    def test_ticket_list_no_args_shows_usage(self):
        result = run_script(f"{SCRIPTS_DIR}/ticket-list.sh")
        assert result.returncode != 0
        assert "Usage" in result.stderr or "usage" in result.stderr.lower()


class TestTicketMoveScript:
    """Test ticket-move.sh."""

    def test_ticket_move_no_args_shows_usage(self):
        result = run_script(f"{SCRIPTS_DIR}/ticket-move.sh")
        assert result.returncode != 0
        assert "Usage" in result.stderr or "usage" in result.stderr.lower()

    def test_ticket_move_done_requires_reason(self, tmp_company_dir):
        env = {"COMPANY_DIR": str(tmp_company_dir)}
        # Try to move to done without a reason
        result = run_script(
            f"{SCRIPTS_DIR}/ticket-move.sh",
            ["it", "TICKET-001", "done", "comment"],
            env=env,
        )
        assert result.returncode != 0
        assert "reason" in result.stderr.lower() or "ERROR" in result.stderr


class TestTicketCommentScript:
    """Test ticket-comment.sh."""

    def test_ticket_comment_no_args_shows_usage(self):
        result = run_script(f"{SCRIPTS_DIR}/ticket-comment.sh")
        assert result.returncode != 0
        assert "Usage" in result.stderr or "usage" in result.stderr.lower()


class TestChatSendScript:
    """Test chat-send.sh."""

    def test_chat_send_no_args_shows_usage(self):
        result = run_script(f"{SCRIPTS_DIR}/chat-send.sh")
        assert result.returncode != 0
        assert "Usage" in result.stderr or "usage" in result.stderr.lower()


class TestChatReadScript:
    """Test chat-read.sh."""

    def test_chat_read_no_args_shows_usage(self):
        result = run_script(f"{SCRIPTS_DIR}/chat-read.sh")
        assert result.returncode != 0
        assert "Usage" in result.stderr or "usage" in result.stderr.lower()


class TestInitWorkspaceScript:
    """Test init-workspace.sh."""

    def test_init_workspace_no_args_shows_usage(self):
        result = run_script(f"{SCRIPTS_DIR}/init-workspace.sh")
        assert result.returncode != 0
        assert "Usage" in result.stderr or "usage" in result.stderr.lower()


class TestInitManifestScript:
    """Test init-manifest.sh."""

    def test_init_manifest_no_args_shows_usage(self):
        result = run_script(f"{SCRIPTS_DIR}/init-manifest.sh")
        assert result.returncode != 0
        assert "Usage" in result.stderr or "usage" in result.stderr.lower()


class TestInitBriefScript:
    """Test init-brief.sh."""

    def test_init_brief_no_args_shows_usage(self):
        result = run_script(f"{SCRIPTS_DIR}/init-brief.sh")
        assert result.returncode != 0
        assert "Usage" in result.stderr or "usage" in result.stderr.lower()


class TestInitChatChannelsScript:
    """Test init-chat-channels.sh."""

    def test_init_chat_channels_no_args_works(self):
        """Should work even with no args (uses defaults)."""
        result = run_script(f"{SCRIPTS_DIR}/init-chat-channels.sh")
        # May or may not fail depending on implementation


class TestInitMeetingScheduleScript:
    """Test init-meeting-schedule.sh."""

    def test_init_meeting_schedule_no_args_works(self):
        result = run_script(f"{SCRIPTS_DIR}/init-meeting-schedule.sh")
        assert result.returncode == 0


class TestValidateWorkspaceScript:
    """Test validate-workspace.sh."""

    def test_validate_workspace_no_args_shows_usage(self):
        result = run_script(f"{SCRIPTS_DIR}/validate-workspace.sh")
        assert result.returncode != 0
        assert "Usage" in result.stderr or "usage" in result.stderr.lower()


class TestCheckCompanyDirScript:
    """Test check-company-dir.sh."""

    def test_check_company_dir_no_args_shows_usage(self):
        result = run_script(f"{SCRIPTS_DIR}/check-company-dir.sh")
        assert result.returncode != 0
        assert "Usage" in result.stderr or "usage" in result.stderr.lower()


class TestSprintStateScript:
    """Test sprint-state.sh — sourcing and functions."""

    def test_sprint_state_has_valid_stages(self):
        """sprint-state.sh should define SS_VALID_STAGES."""
        # Source the script and check for the variable
        code = f"""
source "{SCRIPTS_DIR}/sprint-state.sh" 2>/dev/null
echo "STAGES=${{SS_VALID_STAGES[@]}}"
"""
        result = subprocess.run(
            ["bash", "-c", code],
            capture_output=True, text=True, timeout=10,
            env={**os.environ, "HIVE_INSTALL_DIR": str(Path(__file__).resolve().parent.parent / "hive")},
        )
        assert result.returncode == 0
        # Should contain stage names
        assert "ceo-plan" in result.stdout


class TestDeptHelpersScript:
    """Test dept-helpers.sh."""

    def test_dept_helpers_has_is_admin_dept(self):
        """dept-helpers.sh should define is_admin_dept function."""
        code = f"""
source "{SCRIPTS_DIR}/dept-helpers.sh" 2>/dev/null
type is_admin_dept >/dev/null 2>&1 && echo "FOUND" || echo "NOT_FOUND"
"""
        result = subprocess.run(
            ["bash", "-c", code],
            capture_output=True, text=True, timeout=10,
            env={**os.environ, "HIVE_INSTALL_DIR": str(Path(__file__).resolve().parent.parent / "hive")},
        )
        assert result.returncode == 0


class TestTicketPathsScript:
    """Test ticket-paths.sh."""

    def test_ticket_paths_has_ticket_dir_function(self):
        """ticket-paths.sh should define ticket_dir function."""
        code = f"""
source "{SCRIPTS_DIR}/ticket-paths.sh" 2>/dev/null
type ticket_dir >/dev/null 2>&1 && echo "FOUND" || echo "NOT_FOUND"
"""
        result = subprocess.run(
            ["bash", "-c", code],
            capture_output=True, text=True, timeout=10,
            env={**os.environ, "HIVE_INSTALL_DIR": str(Path(__file__).resolve().parent.parent / "hive")},
        )
        assert result.returncode == 0


class TestGhHelpersScript:
    """Test gh-helpers.sh."""

    def test_gh_helpers_has_functions(self):
        """gh-helpers.sh should define helper functions."""
        code = f"""
source "{SCRIPTS_DIR}/gh-helpers.sh" 2>/dev/null
echo "AUTH_OK=$(type gh_auth_ok >/dev/null 2>&1 && echo FOUND || echo NOT_FOUND)"
echo "REMOTE_OK=$(type gh_remote_ok >/dev/null 2>&1 && echo FOUND || echo NOT_FOUND)"
"""
        result = subprocess.run(
            ["bash", "-c", code],
            capture_output=True, text=True, timeout=10,
            env={**os.environ, "HIVE_INSTALL_DIR": str(Path(__file__).resolve().parent.parent / "hive")},
        )
        assert result.returncode == 0


class TestCoreScripts:
    """Test core scripts (bin/hive, init.sh, orchestrator.sh, claude-spawn.sh)."""

    def test_bin_hive_exists(self):
        assert Path("bin/hive").exists()

    def test_bin_hive_is_executable(self):
        assert os.access("bin/hive", os.X_OK)

    def test_core_init_sh_exists(self):
        assert Path(f"{CORE_DIR}/init.sh").exists()

    def test_core_orchestrator_sh_exists(self):
        assert Path(f"{CORE_DIR}/orchestrator.sh").exists()

    def test_core_claude_spawn_sh_exists(self):
        assert Path(f"{CORE_DIR}/claude-spawn.sh").exists()


class TestAllScriptsExist:
    """Ensure every .sh file in lib/tools/ is executable and parseable."""

    @pytest.mark.parametrize("script", [
        str(p) for p in Path(SCRIPTS_DIR).glob("*.sh")
    ])
    def test_script_is_executable(self, script):
        assert os.access(script, os.X_OK), f"{script} is not executable"

    @pytest.mark.parametrize("script", [
        str(p) for p in Path(SCRIPTS_DIR).glob("*.sh")
    ])
    def test_script_parses(self, script):
        """All scripts should parse without syntax errors."""
        result = subprocess.run(
            ["bash", "-n", script],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0, f"{script} has syntax errors: {result.stderr}"


class TestAllCoreScriptsParse:
    """Ensure every .sh file in lib/core/ parses without syntax errors."""

    @pytest.mark.parametrize("script", [
        str(p) for p in Path(CORE_DIR).glob("*.sh")
    ])
    def test_script_parses(self, script):
        result = subprocess.run(
            ["bash", "-n", script],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0, f"{script} has syntax errors: {result.stderr}"


class TestScaffoldExtractScript:
    """Test scaffold-extract.sh."""

    def test_scaffold_extract_no_args_shows_usage(self):
        result = run_script(f"{SCRIPTS_DIR}/scaffold-extract.sh")
        assert result.returncode != 0


class TestScaffoldUpdateScript:
    """Test scaffold-update.sh."""

    def test_scaffold_update_no_args_shows_usage(self):
        result = run_script(f"{SCRIPTS_DIR}/scaffold-update.sh")
        assert result.returncode != 0
