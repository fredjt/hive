"""Shared fixtures for hive test suite."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure hive lib is on PYTHONPATH for all tests.
_hive_lib = str(Path(__file__).resolve().parent.parent / "lib")
if _hive_lib not in sys.path:
    sys.path.insert(0, _hive_lib)

# Mock mcp module if not available (for environments where it can't be installed)
try:
    import mcp  # noqa: F401
except ImportError:
    from unittest.mock import MagicMock
    
    # Create a mock mcp module covering both client and server APIs
    mock_mcp = MagicMock()
    mock_mcp.ClientSession = MagicMock
    mock_mcp.StdioServerParameters = MagicMock
    mock_mcp.client.stdio = MagicMock()
    mock_mcp.client.streamable_http = MagicMock()
    # Server-side mocks (v1 FastMCP / v2 MCPServer)
    mock_mcp.server.fastmcp.FastMCP = MagicMock
    mock_mcp.server.mcpserver.MCPServer = MagicMock
    sys.modules['mcp'] = mock_mcp
    sys.modules['mcp.client'] = mock_mcp.client
    sys.modules['mcp.client.stdio'] = mock_mcp.client.stdio
    sys.modules['mcp.client.streamable_http'] = mock_mcp.client.streamable_http


def get_install_dir():
    """Return the hive install directory (absolute path)."""
    # The hive source is at the repo root, not in a subdirectory
    return Path(__file__).resolve().parent.parent


@pytest.fixture()
def tmp_project_dir(tmp_path):
    """Create a temporary directory with a minimal .hive scaffold extracted.
    
    Returns the project root (the parent of .hive/).
    """
    install_dir = get_install_dir()
    os.environ["HIVE_INSTALL_DIR"] = str(install_dir)
    
    project_root = tmp_path / "test-project"
    project_root.mkdir()
    
    extract_script = install_dir / "lib" / "tools" / "scaffold-extract.sh"
    if extract_script.exists():
        result = subprocess.run(
            ["bash", str(extract_script), str(project_root)],
            capture_output=True, text=True, timeout=30,
            env={**os.environ, "HIVE_INSTALL_DIR": str(install_dir)},
        )
        assert result.returncode == 0, f"scaffold-extract failed: {result.stderr}"
    else:
        (project_root / ".hive").mkdir()
    
    return project_root


@pytest.fixture()
def tmp_company_dir(tmp_project_dir):
    """Create a minimal .company/ workspace inside the test project.
    
    Returns the company dir path.
    """
    company_dir = tmp_project_dir / ".company"
    company_dir.mkdir()
    
    org_chart = {
        "departments": {
            "it": {"name": "IT", "reporting_to": None},
            "rd": {"name": "R&D", "reporting_to": None},
        },
        "created_at": "2025-01-01T00:00:00Z",
    }
    (company_dir / "org-chart.json").write_text(json.dumps(org_chart, indent=2))
    
    manifest = {
        "project_name": "test-project",
        "mode": "build",
        "auto_mode": False,
    }
    (company_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    
    return company_dir


@pytest.fixture()
def tmp_with_departments(tmp_company_dir):
    """Create department ticket directories for testing.
    
    Returns the departments dir path.
    """
    depts_dir = tmp_company_dir / "departments"
    depts_dir.mkdir()
    
    for status in ["backlog", "sprint-current", "in-progress", "in-review", "done"]:
        (depts_dir / "it" / "tickets" / status).mkdir(parents=True)
    for status in ["backlog", "in-progress"]:
        (depts_dir / "rd" / "tickets" / status).mkdir(parents=True)
    
    return depts_dir


@pytest.fixture()
def sample_ticket():
    """Return a sample ticket dict."""
    return {
        "id": "TICKET-001",
        "title": "Test Ticket",
        "description": "A test ticket for the test suite.",
        "status": "backlog",
        "priority": "medium",
        "type": "feature",
        "created_at": "2025-01-01T00:00:00Z",
        "updated_at": "2025-01-01T00:00:00Z",
    }


@pytest.fixture()
def sample_ticket_json(sample_ticket):
    """Return a sample ticket as JSON string."""
    return json.dumps(sample_ticket, indent=2)


@pytest.fixture()
def sample_org_chart():
    """Return a sample org chart dict."""
    return {
        "departments": {
            "it": {"name": "IT", "reporting_to": None},
            "rd": {"name": "R&D", "reporting_to": None},
            "hr": {"name": "HR", "reporting_to": None},
            "marketing": {"name": "Marketing", "reporting_to": None},
        },
        "created_at": "2025-01-01T00:00:00Z",
    }


@pytest.fixture()
def sample_roster():
    """Return a sample roster dict."""
    return {
        "agents": [
            {"agent_id": "vp-it", "name": "VP-IT", "role": "VP", "department": "it", "status": "active"},
            {"agent_id": "ic-TICKET-001", "name": "Engineer", "role": "IC", "department": "it", "status": "claimed"},
        ],
        "updated_at": "2025-01-01T00:00:00Z",
    }


@pytest.fixture()
def sample_sprint_state():
    """Return a sample sprint state dict."""
    return {
        "sprint_id": "sprint-20250101T000000Z",
        "started_at": "2025-01-01T00:00:00Z",
        "stage": "ceo-plan",
        "stage_started_at": "2025-01-01T00:00:00Z",
        "stage_enqueued": False,
        "loop_iteration": 0,
        "release_retry_count": 0,
        "tickets_in_sprint": [],
        "ceo_direction_path": None,
        "release": {
            "version": None, "test_log": None, "verify_log": None,
            "tarball": None, "sha256": None, "publish_status": "pending",
        },
    }


@pytest.fixture()
def sample_round():
    """Return a sample round JSON."""
    return {
        "entries": [
            {"id": "entry-1", "kind": "ic-cycle-eng", "agent": "ic-engineer",
             "dept": "it", "role": "IC", "ticket": "TICKET-001", "status": "queued"}
        ],
        "cursor": 0,
    }


@pytest.fixture()
def sample_manifest():
    """Return a sample manifest dict."""
    return {
        "project_name": "test-project",
        "mode": "build", "auto_mode": False,
        "created_at": "2025-01-01T00:00:00Z",
    }


@pytest.fixture()
def install_dir():
    """Return the hive install directory."""
    return get_install_dir()


@pytest.fixture(autouse=True)
def setup_hive_env(install_dir):
    """Set HIVE_INSTALL_DIR for all tests."""
    old = os.environ.get("HIVE_INSTALL_DIR")
    os.environ["HIVE_INSTALL_DIR"] = str(install_dir)
    yield
    if old is None:
        os.environ.pop("HIVE_INSTALL_DIR", None)
    else:
        os.environ["HIVE_INSTALL_DIR"] = old


@pytest.fixture()
def temp_dir():
    """Create and clean up a temporary directory."""
    d = tempfile.mkdtemp(prefix="hive-test-")
    yield Path(d)
    shutil.rmtree(d, ignore_errors=True)
