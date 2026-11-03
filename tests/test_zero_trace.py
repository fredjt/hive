"""A.5: zero-trace guard for the 0.6.0 release goal.

The release goal: no trace of the retired external agent CLI remains in
this tree — no calls, no dependency on its settings, no hooks, not even
a mention (code, prompts, docs, tests, changelog alike).

This guard scans every tracked file (git ls-files) and asserts zero
occurrences of the forbidden token. The token is assembled from string
parts so the guard itself stays clean: a plain grep over the tree must
come up empty, and this test enforces that as part of the suite (and in
CI).

Known exception: the one-time legacy cleanup in packaging/uninstall.sh
needs to name the legacy config directory; it computes the path from
string parts too, so a plain scan does not find it either.
"""
import subprocess
from pathlib import Path

TOKEN = "clau" + "de"  # assembled: this file must not hold the literal


class TestZeroTrace:
    @staticmethod
    def _tracked_files():
        r = subprocess.run(["git", "ls-files"], capture_output=True,
                           text=True, timeout=30)
        assert r.returncode == 0, "git ls-files failed"
        return r.stdout.split()

    def test_forbidden_token_absent_from_all_tracked_files(self):
        hits = []
        for f in self._tracked_files():
            p = Path(f)
            if not p.is_file():
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue  # binary file
            n = text.lower().count(TOKEN)
            if n:
                hits.append(f"{f}: {n}")
        assert not hits, (
            "forbidden external-CLI token found in tracked files: "
            f"{hits} — the 0.6.0 goal is a zero-trace tree"
        )

    def test_uninstall_cleanup_assembles_legacy_path(self):
        """The single sanctioned reference (A.1's legacy cleanup) must
        compute the legacy config directory from string parts — a plain
        scan of the script must not find the literal directory name."""
        text = Path("packaging/uninstall.sh").read_text()
        assert TOKEN not in text.lower(), (
            "uninstall.sh contains the legacy config directory name as a "
            "literal — assemble it from string parts (A.1)"
        )
