"""hive roster — who is working on what (0.18 T2).

A read-only, CLI-only surface over the existing `roster_get` MCP tool:
the same script path (roster-update.sh list, including the
rebuild-before-read), so `roster_get` and the script are untouched —
no MCP wire change, zero agent-side diff. The CLI adds the human
rendering and the named empty states (the 0.17 T3 family):

  * no `.company` → the init hint (exit 1 — checked BEFORE the call;
    today a no-workspace dir lands on the script's silent `[]`, rc 0);
  * workspace present but roster.json still absent AFTER the read (the
    rebuild failed — the script's `|| true` swallowed it) → the named
    'roster unavailable' error (exit 1; today: the same silent `[]`);
  * derived-but-empty roster (file present, zero agents) → the
    count-0 listing (exit 0 — visible, not silent);
  * a named department that does not exist → exit 1 + the known-dept
    list (the 0.17 T2 contract, same family).

Two faces like the other workhorses: the default is the human table
(NAME / ROLE / DEPT / STATUS / TICKET / LAST ACTIVE, active rows
first, then claimed, then inactive, count line at the end); `--json`
is the `.agents` array exactly as `roster_get` returns it (honoring
`--active` and `--dept`), so the CLI and the MCP side share one
byte-identical contract.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# T1 (0.20): mcp_client imports in the handler body only — present is
# pure rendering (SDK-free) and stays at module level.
from hive_cli import present
from hive_cli.commands import company_dir as _company_dir


def _known_depts(roster_file: Path) -> list[str]:
    """The departments with a seat at the table in the roster document
    (the derivation's own answer — org-chart, runtime dirs, and the
    always-present static set, post-rebuild)."""
    try:
        doc = json.loads(roster_file.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    return sorted({a.get("department") for a in doc.get("agents", [])
                   if a.get("department")})


def handle(args) -> int:
    from hive_cli import mcp_client
    cd = _company_dir()
    # The CLI checks before calling: today a no-workspace dir lands on
    # the script's silent `[]`, rc 0 — the init hint is the answer.
    if not cd or not Path(cd).is_dir():
        print("No .company here — run 'hive init' to scaffold this project.",
              file=sys.stderr)
        return 1
    roster_file = Path(cd) / "roster.json"
    payload: dict = {"company_dir": cd}
    if getattr(args, "active", False):
        payload["active_only"] = True
    if getattr(args, "dept", None):
        payload["dept"] = args.dept
    res = mcp_client.call_tool("roster_get", payload)
    if not res.get("ok"):
        print(res.get("stderr") or res.get("error") or "roster_get failed",
              file=sys.stderr)
        return 1
    # The read ran its rebuild first — if roster.json is STILL absent,
    # the rebuild failed and the script swallowed it (`|| true`):
    # 'rebuild broken' must be named, not indistinguishable from
    # 'no agents'.
    if not roster_file.is_file():
        print("roster unavailable — the rebuild failed; run 'hive doctor' "
              "(the roster derives from tickets, VP logs and worktrees "
              "every 60s while the company runs)", file=sys.stderr)
        return 1
    agents = res.get("data")
    if not isinstance(agents, list):
        agents = []
    if getattr(args, "dept", None):
        known = _known_depts(roster_file)
        if args.dept not in known:
            print(f"ERROR: Department '{args.dept}' not found "
                  f"(available: {', '.join(known)})", file=sys.stderr)
            return 1
    if getattr(args, "json", False):
        # The machine face: the .agents array exactly as roster_get
        # returns it — the CLI and the MCP side share one document.
        print(json.dumps(agents, indent=2, ensure_ascii=False))
        return 0
    print(present.render_roster(agents))
    return 0