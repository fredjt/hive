"""T5 (0.22): the CI surface of the dashboard floor.

The 3.14 full row (the row that proves the new version runs the whole
thing) and the browser-legs job (the 0.21+ dashboard floor as a GATE,
not a receipt artifact). The job installs nothing — the runner's Chrome
plus node builtins (fetch + the built-in WebSocket) are the whole
toolchain; the zero-dependency posture is a contract these guards pin.
"""
import shutil
import subprocess

import pytest
from pathlib import Path

WORKFLOW = Path(".github/workflows/ci.yml")
DRIVER = Path("tests/cdp_legs.js")


class TestCiThreeFullRows:
    def test_matrix_carries_the_314_full_row(self):
        text = WORKFLOW.read_text()
        assert "- python: '3.14'\n            suite: full" in text, (
            "the CI matrix must carry the 3.14 FULL row — the row that "
            "proves the new version runs the whole thing")
        assert text.count("suite: full") >= 3, (
            "all three matrix rows (3.12, 3.13, 3.14) run the full suite")


class TestCiBrowserJob:
    def test_browser_job_exists_and_installs_nothing(self):
        text = WORKFLOW.read_text()
        assert "\n  browser:" in text, "the browser job must exist in ci.yml"
        job = text.split("\n  browser:", 1)[1].split("\n  lint:", 1)[0]
        for banned in ("apt-get", "npm install", "npx playwright",
                       "puppeteer", "wget", "curl -O"):
            assert banned not in job, (
                f"the browser job installs nothing — '{banned}' in the job "
                "body breaks the zero-install contract (the runner's "
                "Chrome + node builtins are the whole toolchain)")
        assert "google-chrome" in job, (
            "the job locates the RUNNER'S Chrome by grepping PATH for "
            "the standard names (google-chrome ... chromium-browser)")
        assert "tests/cdp_legs.js" in job, (
            "the job drives the zero-dep driver tests/cdp_legs.js")
        assert "--ghbase" in job, (
            "the job runs the gh-absent server (a PATH without gh)")
        assert "cdp_legs.js" in job, "the job drives tests/cdp_legs.js"

    def test_driver_exists_and_parses(self):
        """The job's driver must exist in the tree and parse under plain
        node (the same posture as the node-check job for server.js):
        a driver that cannot load would fail the CI job with a stack
        trace where a named guard says what is missing."""
        assert DRIVER.is_file(), (
            "tests/cdp_legs.js is missing — the browser job drives it")
        if shutil.which("node") is None:
            pytest.skip("node not installed (the CI job brings its own)")
        r = subprocess.run(["node", "--check", str(DRIVER)],
                           capture_output=True, text=True, timeout=30)
        assert r.returncode == 0, (
            "node --check tests/cdp_legs.js fails (the driver must be "
            "plain-parseable node — zero deps, no build step):\n"
            + r.stderr[:500])
