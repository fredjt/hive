#!/usr/bin/env python3
"""Regenerate the committed SPA smoke fixtures (C2).

Usage:  python tests/gen_spa_fixtures.py

For each fixture tree (healthy, degenerate) this copies the tree under a
tmp project root with the deterministic scratch git repo, starts the real
dashboard server, and runs the record mode of tests/spa_smoke.js — the
genesis is the consumer, so the fixtures can never drift from what the
SPA actually requests. The recorded manifests are written to
tests/fixtures/spa/<tree>/manifest.json. Review the diff before committing:
a non-trivial diff means the server's API shape (or the SPA's requests)
changed, which is exactly what C1/C2 are meant to surface.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests"))

import test_spa_smoke as t  # noqa: E402


def main():
    node = t.TestSpaSmoke.node
    if not node:
        sys.exit("node unavailable — cannot regenerate the SPA fixtures")
    me = t.TestSpaSmoke()
    with tempfile.TemporaryDirectory(prefix="gen-spa-") as td:
        tmp = Path(td)
        proj = me._copy_project(tmp)
        for name in ("healthy", "degenerate"):
            company = proj / ("company" if name == "healthy" else "company-degenerate")
            server, port = me._start_server(company)
            try:
                out = t.SPA_FIX_DIR / name / "manifest.json"
                r = me._run_smoke(
                    "record", out, tmp / f"gen-report-{name}.json",
                    ["--tree", name,
                     "--base", f"http://127.0.0.1:{port}",
                     "--root", str(proj),
                     "--out", str(out)])
                rep_ok = False
                if (tmp / f"gen-report-{name}.json").exists():
                    rep = json.loads((tmp / f"gen-report-{name}.json").read_text())
                    rep_ok = bool(rep.get("ok"))
                if r.returncode != 0 or not rep_ok:
                    sys.exit(f"record run for {name} failed (rc={r.returncode}, ok={rep_ok})\n{r.stdout[-2000:]}")
                print(f"wrote {out.relative_to(REPO)} — views ok")
            finally:
                server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()


if __name__ == "__main__":
    main()
