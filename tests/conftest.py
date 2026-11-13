"""Shared fixtures for hive test suite."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Shared clock normalization (the xdist worker contract, R1/0.11)
#
# Any test that compares state written at different instants (two move
# paths, two runs, a write observed across processes) MUST normalize
# wall-clock timestamps with normalize_clock() before comparing. The
# writes run a few ms apart and cross a second boundary on ~5% of runs;
# the contract is identical STATE, not identical INSTANT. Do not
# hand-roll a second normalizer in a test module — the static guard in
# tests/test_xdist_contract.py fails on one.
# ---------------------------------------------------------------------------

#: Keys whose values are wall-clock timestamps — the instant the state was
#: written, not part of the state's identity.
TIMESTAMP_KEYS = ("created_at", "updated_at", "started_at",
                  "resolved_at", "stage_started_at", "timestamp")

#: Keys whose values are wall-clock instants in EPOCH MILLISECONDS — a
#: file's mtime and the age derived from it (the dashboard's /api/config
#: snapshot freshness). Same contract as TIMESTAMP_KEYS: the instant is
#: not part of the state's identity, so it is normalized to the same
#: constant "T" before any cross-instant comparison (the SPA fixture
#: drift guard compares two records of the same state written at
#: different instants).
FRESHNESS_KEYS = ("mtime_ms", "age_ms")


def normalize_clock(value, timestamp_keys=TIMESTAMP_KEYS,
                    freshness_keys=FRESHNESS_KEYS):
    """Replace wall-clock timestamps with the constant "T".

    String instants in timestamp_keys and numeric (epoch-ms) instants in
    freshness_keys are both replaced — the instant is not part of the
    state's identity. Recurses through dicts and lists. Used before
    comparing two pieces of state written at different instants (the two
    shared move paths in test_state_layer), so the comparison asserts
    identical move paths — not identical instants.
    """
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k in timestamp_keys and isinstance(v, str):
                out[k] = "T"
            elif (k in freshness_keys
                  and isinstance(v, (int, float))
                  and not isinstance(v, bool)):
                out[k] = "T"
            else:
                out[k] = normalize_clock(v, timestamp_keys, freshness_keys)
        return out
    if isinstance(value, list):
        return [normalize_clock(v, timestamp_keys, freshness_keys)
                for v in value]
    return value

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


@pytest.fixture(scope="session", autouse=True)
def _basetemp_retention(tmp_path_factory):
    """T4 (0.14): the workspace reclaims its own scratch — basetemp
    retention by construction.

    pytest keeps every session's basetemp dir (pytest-of-<user>/pytest-<N>
    and the xdist workers' dirs), and nothing reclaims them: the
    scratch snowballs one generation per session. This hook runs once
    per session, keeps the newest 3 basetemp generations under the
    basetemp root, and removes older ones — under a fresh-mtime gate
    (60 min): a running suite's own dirs, and any dir a concurrent
    session or worker just created, are never touched (the xdist
    workers each run this hook; the fresh-mtime gate makes the
    concurrent rmtree no-ops, ignore_errors absorbs the race).
    The operator-facing tool is the same policy:
    packaging/clean-scratch.sh."""
    root = tmp_path_factory.getbasetemp().parent
    gens = {}
    for d in root.iterdir():
        try:
            if not d.is_dir():
                continue
        except OSError:
            continue
        m = re.match(r"^pytest-(\d+)(?:-gw\d+)?$", d.name)
        if m:
            gens.setdefault(int(m.group(1)), []).append(d)
    keep = set(sorted(gens, reverse=True)[:3])
    for n, dirs in gens.items():
        if n in keep:
            continue
        for d in dirs:
            try:
                if time.time() - d.stat().st_mtime < 60 * 60:
                    continue  # fresh — a live session may own it
                shutil.rmtree(d, ignore_errors=True)
            except OSError:
                continue
    yield


@pytest.fixture(scope="session", autouse=True)
def _venv_lock_env(tmp_path_factory):
    """T1 (0.14): one venv per session, built from the repo's venv lock,
    for every build-package.sh spawn.

    build-package.sh self-verifies the lock (T1): the producer (the cut)
    builds a FRESH venv from the lock and imports the runtime set — a
    network install. The suite pays that cost ONCE per session: this
    fixture builds the venv and points HIVE_LOCK_VENV at it, and the
    build verifies the venv's resolved versions against the lock in
    place of the fresh install. The fresh-venv path itself stays
    covered: TestBuildLockVerify's dedicated fixture runs one full
    build WITHOUT the variable. Pre-lock trees (no lock file) build
    nothing — the build fails at the missing-lock check, which is what
    the tests assert.

    The venv dir is named hive-lock-venv* under the scratch basetemp
    (TMPDIR): T4's clean-scratch reaps the mktemp-shaped litter
    (hive-*) older than N days, so a finished session leaves nothing
    behind."""
    repo = get_install_dir()
    lock = repo / "packaging" / "requirements.lock.txt"
    if not lock.is_file():
        yield None
        return
    venv_dir = tmp_path_factory.mktemp("hive-lock-venv") / "venv"
    r = subprocess.run([sys.executable, "-m", "venv", str(venv_dir)],
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, (
        f"could not create the session lock venv:\n{r.stderr}")
    r = subprocess.run([str(venv_dir / "bin" / "python"), "-m", "pip",
                        "install", "--quiet", "-r", str(lock)],
                       capture_output=True, text=True, timeout=900)
    assert r.returncode == 0, (
        "the session lock venv could not install the lock — the lock is "
        f"not installable:\n{r.stderr[-3000:]}")
    old = os.environ.get("HIVE_LOCK_VENV")
    os.environ["HIVE_LOCK_VENV"] = str(venv_dir)
    yield str(venv_dir)
    if old is None:
        os.environ.pop("HIVE_LOCK_VENV", None)
    else:
        os.environ["HIVE_LOCK_VENV"] = old


@pytest.fixture()
def tmp_project_dir(tmp_path):
    """Create a temporary project directory.
    
    Projects now use installed scripts directly from HIVE_INSTALL_DIR;
    no per-project .hive/ scaffold is needed.
    
    Returns the project root.
    """
    install_dir = get_install_dir()
    os.environ["HIVE_INSTALL_DIR"] = str(install_dir)
    
    project_root = tmp_path / "test-project"
    project_root.mkdir()
    
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
