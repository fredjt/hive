"""Release pipeline tools — read-only via MCP under sprint-staged lifecycle.

Lifecycle control (release_start / release_publish / release_abort) is
intentionally NOT exposed via MCP. The release pipeline runs as stages 6-8
of every sprint (build-and-test → release-verify → release-publish), driven
by sprint-lifecycle.sh. Agents shouldn't initiate releases — the sprint cycle
does.

Exposed:
  release_status     — read current/historical release state
  release_history    — list recent published releases
  release_verdict    — release-ic writes its GO/NO-GO verdict here.
                       (The verdict file lives under .company/releases/.
                       This tool wraps release-verdict-write.sh.)
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from hive_mcp.shell import run


def register(server: Server) -> None:
    @server.tool()
    def release_verdict(
        verdict: str,
        rationale: str,
        blockers: str | None = None,
        company_dir: str | None = None,
    ) -> dict:
        """Write the release-ic GO/NO-GO verdict for the current sprint's
        release-verify stage. sprint-lifecycle reads the first VERDICT: line
        and routes the sprint to release-publish (GO) or ceo-bugfix-plan
        (NO-GO).

        Args:
          verdict:   "GO" or "NO-GO"
          rationale: one-paragraph explanation. For GO, name two edge
                     cases probed and not broken. For NO-GO, summarize
                     what failed.
          blockers:  optional (only meaningful for NO-GO). Newline-
                     separated bullet list of concrete blockers, one
                     per line (e.g. "- packaging/install.sh:42 — fix X").
                     The script prepends a "BLOCKERS:" header.

        Returns the absolute path of the verdict file written.
        """
        args = [verdict, "--rationale", rationale]
        if blockers:
            args += ["--blockers-stdin"]
            return run("release-verdict-write", *args, stdin=blockers,
                       company_dir=company_dir).to_dict()
        return run("release-verdict-write", *args, company_dir=company_dir).to_dict()

    @server.tool()
    def release_status(version: str | None = None,
                       company_dir: str | None = None) -> dict:
        """Read sprint state's release block, or a historical release file."""
        cd = company_dir or os.environ.get("COMPANY_DIR") or str(Path.cwd() / ".company")
        # Prefer current sprint state (which has the in-flight release block)
        sprint_cur = Path(cd) / "sprints" / "current.json"
        if version is None and sprint_cur.is_file():
            try:
                d = json.loads(sprint_cur.read_text())
                return {"sprint_id": d.get("sprint_id"), "stage": d.get("stage"),
                        "release": d.get("release", {})}
            except Exception as e:
                return {"error": str(e)}
        # Fall back to a historical sprint by version match
        hist = Path(cd) / "sprints" / "history"
        if hist.is_dir() and version:
            for f in sorted(hist.glob("sprint-*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
                try:
                    d = json.loads(f.read_text())
                    if d.get("release", {}).get("version") == version:
                        return {"sprint_id": d.get("sprint_id"), "stage": d.get("stage"),
                                "release": d.get("release", {})}
                except Exception:
                    continue
        return {"error": "no release record found"}

    @server.tool()
    def release_history(limit: int = 20, company_dir: str | None = None) -> dict:
        """List recent published releases from sprints/history/."""
        cd = company_dir or os.environ.get("COMPANY_DIR") or str(Path.cwd() / ".company")
        hist = Path(cd) / "sprints" / "history"
        out = []
        if hist.is_dir():
            files = sorted(hist.glob("sprint-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
            for f in files:
                try:
                    d = json.loads(f.read_text())
                    rel = d.get("release", {}) or {}
                    if not rel.get("version"):
                        continue
                    out.append({
                        "sprint_id": d.get("sprint_id"),
                        "version": rel.get("version"),
                        "publish_status": rel.get("publish_status"),
                        "tarball": rel.get("tarball"),
                        "ended_at": d.get("ended_at"),
                    })
                except Exception:
                    pass
        return {"releases": out}
