"""C2 (0.9.0): one fixture set, two consumers — drift guard.

The canned fetch responses the SPA smoke (C1) replays are GENERATED from
the same fixture .company trees the server-side API tests use, by the
record mode of the smoke runner itself (tests/spa_smoke.js --mode
record), never copy-pasted. This test re-runs that generator against the
real dashboard server and requires the live responses to agree with the
committed manifest — so a server-side field rename (say started_at ->
started in /api/rounds/history) breaks the SPA fixture in CI, and the
browser is never the first consumer of a new shape. The committed
manifest is regenerated with `python tests/gen_spa_fixtures.py`.
"""
import json
import subprocess

import pytest

# The C1 module owns the shared fixture locations, the node/git
# availability checks, and the scratch-project + server helpers.
from tests.test_spa_smoke import (
    SPA_FIX_DIR,
    TestSpaSmoke as _Helpers,
)


class TestSpaFixtureDrift:
    node = _Helpers.node
    git = _Helpers.git

    def _needs_node(self):
        if not self.node:
            pytest.skip("node unavailable — cannot run the SPA smoke")

    def test_recorded_fixtures_match_live_server(self, tmp_path):
        """Re-generate every manifest against the real dashboard server
        and require agreement (parsed-JSON equality) with the committed
        fixture. A server-side field rename drifts the live shape from
        the committed one and fails here; a rename the SPA ALSO adopted
        fails the record run itself (literal 'undefined' in the rendered
        output, or a view that throws).

        The generator is a fixture, not copy-paste: this test runs the
        record mode of the very smoke that consumes the manifest."""
        self._needs_node()
        if not self.git:
            pytest.skip("git unavailable — cannot build the scratch repo")
        me = _Helpers()
        proj = me._copy_project(tmp_path)
        for name in ("healthy", "degenerate"):
            company = proj / ("company" if name == "healthy" else "company-degenerate")
            server, port = me._start_server(company)
            try:
                fresh = tmp_path / f"fresh-{name}.json"
                r = me._run_smoke(
                    "record", fresh, tmp_path / f"record-report-{name}.json",
                    ["--tree", name,
                     "--base", f"http://127.0.0.1:{port}",
                     "--root", str(proj),
                     "--out", str(fresh)])
                assert r.returncode == 0, (
                    f"record run for {name} failed — the SPA cannot render "
                    f"against the live server:\n{r.stdout[-3000:]}")
            finally:
                server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()
            committed = SPA_FIX_DIR / name / "manifest.json"
            assert committed.exists(), (
                f"missing committed SPA fixture {committed} — generate it "
                "with: python tests/gen_spa_fixtures.py")
            a = json.loads(fresh.read_text())
            b = json.loads(committed.read_text())
            assert set(a["responses"]) == set(b["responses"]), (
                f"{name}: endpoint set drifted — live server serves "
                f"{sorted(set(a['responses']) ^ set(b['responses']))}")
            for key in sorted(a["responses"]):
                assert a["responses"][key] == b["responses"][key], (
                    f"{name}: /api/{key} response shape drifted from the "
                    f"committed SPA fixture — the server's JSON changed "
                    "(field rename?); regenerate the fixture with "
                    "python tests/gen_spa_fixtures.py or revert the "
                    "server change")
