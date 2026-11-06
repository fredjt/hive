"""Item 9: org-chart departments reach the roster.

org-chart.json's .departments is a keyed object (name -> metadata), but
roster-rebuild read it with '.departments[]?.name' - which yields nothing
for a keyed object - so org-chart departments never reached roster.json
(vp-finance / vp-legal missing despite being spawned and active).
"""
import json
import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ROSTER = REPO / "lib" / "tools" / "roster-rebuild.sh"


def test_org_chart_keyed_departments_reach_roster(tmp_path):
    company = tmp_path / ".company"
    (company / "logs").mkdir(parents=True)
    # finance + legal exist ONLY in the org chart (keyed object form).
    org = {
        "project_name": "t",
        "departments": {
            "finance": {"type": "fixed", "vp": "VP-Finance"},
            "legal": {"type": "fixed", "vp": "VP-Legal"},
        },
    }
    (company / "org-chart.json").write_text(json.dumps(org))

    env = {**os.environ, "COMPANY_DIR": str(company)}
    r = subprocess.run(["bash", str(ROSTER), "--quiet"], env=env,
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    roster = json.loads((company / "roster.json").read_text())
    ids = [a.get("agent_id") for a in roster.get("agents", [])]
    assert "vp-finance" in ids, f"org-chart dept missing from roster: {ids}"
    assert "vp-legal" in ids, f"org-chart dept missing from roster: {ids}"
