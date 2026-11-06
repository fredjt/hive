"""E2: the org chart's name re-derives from the manifest.

The 0.10.0 field run's org chart said "Untitled Project" for the lifetime
of the company: init-workspace.sh rebuilt the org chart BEFORE
init-manifest.sh created the manifest (the "Untitled Project" stamp), and
org-chart-rebuild.sh's compose preserved the existing project_name over
the manifest-derived value forever after. Now: the manifest wins in the
compose, and init creates the manifest before the org chart rebuild.
"""
import json
import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
INIT = REPO / "lib" / "core" / "init.sh"
ORGC = REPO / "lib" / "tools" / "org-chart-rebuild.sh"


def _org(cd):
    f = Path(cd) / "org-chart.json"
    return json.loads(f.read_text()) if f.exists() else None


def test_init_names_the_org_chart_directly(tmp_path):
    """hive init --name foo -> org-chart.json's project_name is foo
    directly after init (no rebuild cycle needed)."""
    proj = tmp_path / "proj"
    proj.mkdir()
    # Item 5: init guard - a company is its own git repo (the tmp dir
    # sits inside the sandbox checkout).
    subprocess.run(["git", "init", "-q"], cwd=str(proj), check=True)
    r = subprocess.run(
        ["bash", str(INIT), str(proj), "--brief", "test", "--name", "foo",
         "--github", "skip"],
        capture_output=True, text=True, timeout=180,
        cwd=str(proj),
        env={**os.environ, "HIVE_ROOT": str(REPO),
             "HIVE_INSTALL_DIR": str(REPO)})
    assert r.returncode == 0, (r.stderr or r.stdout)[-2000:]
    org = _org(proj / ".company")
    assert org is not None, "init did not create org-chart.json"
    assert org["project_name"] == "foo", org
    manifest = json.loads((proj / ".company" / "manifest.json").read_text())
    assert manifest["project_name"] == "foo"


def test_stale_org_chart_name_corrected_on_first_rebuild(tmp_path):
    """A pre-existing org chart with a stale name + a manifest with the
    real one -> the first rebuild corrects it (the run's exact failure)."""
    cd = tmp_path / ".company"
    cd.mkdir()
    (cd / "manifest.json").write_text(json.dumps({
        "project_name": "Real Project", "status": "build", "mode": "build",
        "auto_mode": True, "created_at": "t", "updated_at": "t",
        "cycle_count": 0, "current_sprint": None, "perpetual_since": None,
        "idle_mode": False}))
    (cd / "org-chart.json").write_text(json.dumps({
        "project_name": "Untitled Project",
        "company_name": "Untitled Project Co.",
        "departments": {}}))
    r = subprocess.run(["bash", str(ORGC), "--quiet"], capture_output=True,
                       text=True, timeout=30,
                       env={**os.environ, "COMPANY_DIR": str(cd)})
    assert r.returncode == 0, r.stderr
    org = _org(cd)
    assert org["project_name"] == "Real Project", org


def test_manifest_less_rebuild_keeps_existing_name(tmp_path):
    """No manifest + an existing real name -> the rebuild must NOT
    clobber it with the placeholder."""
    cd = tmp_path / ".company"
    cd.mkdir()
    (cd / "org-chart.json").write_text(json.dumps({
        "project_name": "Kept Name", "company_name": "Kept Name Co.",
        "departments": {}}))
    r = subprocess.run(["bash", str(ORGC), "--quiet"], capture_output=True,
                       text=True, timeout=30,
                       env={**os.environ, "COMPANY_DIR": str(cd)})
    assert r.returncode == 0, r.stderr
    org = _org(cd)
    assert org["project_name"] == "Kept Name", org
