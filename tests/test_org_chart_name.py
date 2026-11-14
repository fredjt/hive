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


FIXED_DEPTS = ["hr", "it", "rd", "marketing", "legal", "finance"]


def _rebuild(cd):
    return subprocess.run(["bash", str(ORGC), "--quiet"], capture_output=True,
                          text=True, timeout=30,
                          env={**os.environ, "COMPANY_DIR": str(cd)})


class TestOrgChartWorkKind:
    """W2 (0.23): the org chart declares the division of labor.

    Each dept entry carries `kind` (code|deliverable) — the resolver's
    middle rung. The scaffold writes the default per provenance: the
    fixed six are documentation departments (deliverable), a project's
    engineering depts (dynamic) default to code. A CEO-set kind
    survives a rebuild; an invalid kind is not written."""

    def test_fresh_rebuild_carries_kinds_by_provenance(self, tmp_path):
        """A fresh company's org chart DECLARES its division of labor:
        all six fixed depts deliverable, a discovered dynamic dept code."""
        cd = tmp_path / ".company"
        (cd / "departments" / "backend-api-services" / "tickets"
         / "backlog").mkdir(parents=True)
        r = _rebuild(cd)
        assert r.returncode == 0, r.stderr
        org = _org(cd)
        for d in FIXED_DEPTS:
            assert org["departments"][d].get("kind") == "deliverable", \
                f"fixed dept {d}: {org['departments'][d]!r}"
        dyn = org["departments"]["backend-api-services"]
        assert dyn.get("kind") == "code", dyn

    def test_ceo_set_kind_survives_rebuild(self, tmp_path):
        """A manager can re-point a dept's default kind with one
        org-chart edit — the rebuild preserves it (the way it already
        preserves CEO-authored depts)."""
        cd = tmp_path / ".company"
        cd.mkdir()
        (cd / "org-chart.json").write_text(json.dumps({
            "project_name": "P", "company_name": "P Co.",
            "departments": {
                # CEO override: the R&D dept does code (prototypes)
                "rd": {"type": "fixed", "vp": "VP-R&D", "kind": "code",
                       "purpose": "Research, competitive analysis, new technology"},
                # CEO override the other way: an engineering dept that
                # files docs
                "backend-api-services": {"type": "dynamic", "vp": "VP-Backend",
                                         "kind": "deliverable",
                                         "purpose": "API platform"},
            }}))
        (cd / "departments" / "backend-api-services" / "tickets").mkdir(parents=True)
        r = _rebuild(cd)
        assert r.returncode == 0, r.stderr
        org = _org(cd)
        assert org["departments"]["rd"].get("kind") == "code", \
            "the CEO-set kind on a fixed dept must survive the rebuild"
        assert org["departments"]["backend-api-services"].get("kind") == "deliverable"

    def test_invalid_kind_is_not_written(self, tmp_path):
        """An invalid kind value is replaced with the valid default at
        the compose step — the rebuild never fails on it, never writes
        it (the resolver's fallback would have made the same decision
        anyway)."""
        cd = tmp_path / ".company"
        cd.mkdir()
        (cd / "org-chart.json").write_text(json.dumps({
            "project_name": "P", "company_name": "P Co.",
            "departments": {
                "rd": {"type": "fixed", "vp": "VP-R&D", "kind": "purple",
                       "purpose": "Research"},
                "backend-api-services": {"type": "dynamic", "vp": "VP-Backend",
                                         "kind": "purple", "purpose": "API"},
            }}))
        (cd / "departments" / "backend-api-services" / "tickets").mkdir(parents=True)
        r = _rebuild(cd)
        assert r.returncode == 0, \
            f"a garbage kind must not fail the rebuild: {r.stderr}"
        org = _org(cd)
        assert org["departments"]["rd"].get("kind") == "deliverable", \
            "invalid kind on a fixed dept must be replaced by the fixed default"
        assert org["departments"]["backend-api-services"].get("kind") == "code", \
            "invalid kind on a dynamic dept must be replaced by the dynamic default"

    def test_absent_kind_filled_from_provenance(self, tmp_path):
        """A pre-0.23 chart (no kind anywhere) gains the valid defaults
        on the next rebuild — silently: the legacy name list gave every
        one of these depts the same pipeline."""
        cd = tmp_path / ".company"
        cd.mkdir()
        (cd / "org-chart.json").write_text(json.dumps({
            "project_name": "P", "company_name": "P Co.",
            "departments": {
                "hr": {"type": "fixed", "vp": "VP-HR", "purpose": "Hiring"},
                "backend-api-services": {"type": "dynamic", "vp": "VP-Backend",
                                         "purpose": "API"},
            }}))
        (cd / "departments" / "backend-api-services" / "tickets").mkdir(parents=True)
        r = _rebuild(cd)
        assert r.returncode == 0, r.stderr
        org = _org(cd)
        assert org["departments"]["hr"].get("kind") == "deliverable"
        assert org["departments"]["backend-api-services"].get("kind") == "code"
        # every fixed dept present on the rebuilt chart carries the default
        for d in FIXED_DEPTS:
            assert org["departments"][d].get("kind") == "deliverable", d


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
