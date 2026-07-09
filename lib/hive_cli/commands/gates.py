"""hive gates ..."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from hive_cli import mcp_client


def _cd():
    p = Path.cwd() / ".company"
    return str(p) if p.is_dir() else None


def handle(args) -> int:
    cd = _cd()
    if args.action == "run":
        payload = {"dept": args.dept, "project_root": str(Path(args.project_dir).resolve())}
        if cd: payload["company_dir"] = cd
        res = mcp_client.call_tool("gates_run", payload)
        print(json.dumps(res, indent=2))
        return 0 if res.get("ok") else 1

    if args.action == "check":
        payload = {"dept": args.dept, "ticket_id": args.ticket_id}
        if cd: payload["company_dir"] = cd
        res = mcp_client.call_tool("gates_check", payload)
        print(json.dumps(res, indent=2))
        return 0 if res.get("ok") else 1

    if args.action == "status":
        company_dir = cd or os.environ.get("COMPANY_DIR") or str(Path.cwd() / ".company")
        depts_dir = Path(company_dir) / "departments"
        if not depts_dir.is_dir():
            print(f"ERROR: no departments directory at {depts_dir}", file=sys.stderr)
            return 1

        cfg_path = Path(company_dir) / "gates.json"
        cfg = {}
        if cfg_path.is_file():
            try:
                cfg = json.loads(cfg_path.read_text())
            except json.JSONDecodeError:
                pass
        print(f"Gates config ({cfg_path}):")
        print(f"  mode:             {cfg.get('mode', '(unset = relaxed)')}")
        for k in ("require_tests", "require_lint", "require_security", "require_commits"):
            print(f"  {k:<17} {cfg.get(k, False)}")
        print()

        any_review = False
        for dept_dir in sorted(depts_dir.iterdir()):
            if not dept_dir.is_dir():
                continue
            review_dir = dept_dir / "tickets" / "in-review"
            if not review_dir.is_dir():
                continue
            tickets = sorted(review_dir.glob("*.json"))
            if not tickets:
                continue
            any_review = True
            print(f"  {dept_dir.name}: {len(tickets)} in-review")
            for tf in tickets:
                try:
                    t = json.loads(tf.read_text())
                except json.JSONDecodeError:
                    continue
                tid = t.get("id", tf.stem)
                gates_passed = t.get("gates_passed", False)
                verified_by = t.get("verified_by", "")

                gates_file = (dept_dir / "components" / tid / "gates.json")
                gate_summary = ""
                if gates_file.is_file():
                    try:
                        gd = json.loads(gates_file.read_text())
                        gates = gd.get("gates", {})
                        passes = sum(1 for g in gates.values() if g.get("status") == "pass")
                        fails  = [n for n, g in gates.items() if g.get("status") == "fail"]
                        skips  = sum(1 for g in gates.values() if g.get("status") == "skip")
                        if fails:
                            gate_summary = f"  [FAIL: {', '.join(fails)}]"
                        else:
                            gate_summary = f"  [pass={passes} skip={skips}]"
                    except json.JSONDecodeError:
                        pass

                if not gates_passed:
                    state = "BLOCKED (gates not passed)"
                elif not verified_by:
                    state = "awaiting verification IC"
                else:
                    state = f"approved by {verified_by} (waiting for ticket-move done)"
                print(f"    {tid:<25} {state}{gate_summary}")
            print()

        if not any_review:
            print("  No in-review tickets across any department.")
        return 0

    print(f"unknown gates action: {args.action}", file=sys.stderr)
    return 1
