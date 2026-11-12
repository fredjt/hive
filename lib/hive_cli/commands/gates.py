"""hive gates ...

Two faces (T1): run/check print the 0.16.0 envelope under --json and
the human summary by default (per-gate lines for check, per-ticket
summary lines for run); status was already human and stays so, with
--json gaining the structured envelope over the same data.
Exit codes unchanged on every path.

T3 (0.19): the run's human face is the renderer's summary only (the
raw log lines stay in the --json envelope); the check's no-report
failure — the common case, every ticket before its first gate run —
gets a named face; status renders its config booleans in the config
table's spelling (true/false).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from hive_cli import mcp_client, present
from hive_cli.commands import company_dir as _company_dir


def _collect_gates_status(company_dir: str) -> dict:
    """The gates-status view as data: the gates.json config plus one row
    per department with in-review tickets (id, state, gate summary)."""
    cfg_path = Path(company_dir) / "gates.json"
    cfg = {}
    if cfg_path.is_file():
        try:
            cfg = json.loads(cfg_path.read_text())
        except json.JSONDecodeError:
            pass
    cfg = dict(cfg)
    cfg["_path"] = str(cfg_path)

    depts_dir = Path(company_dir) / "departments"
    rows = []
    for dept_dir in sorted(depts_dir.iterdir()):
        if not dept_dir.is_dir():
            continue
        review_dir = dept_dir / "tickets" / "in-review"
        if not review_dir.is_dir():
            continue
        tickets = sorted(review_dir.glob("*.json"))
        if not tickets:
            continue
        trows = []
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
            trows.append({"id": tid, "state": state, "gate_summary": gate_summary,
                          "gates_passed": gates_passed, "verified_by": verified_by})
        rows.append({"dept": dept_dir.name, "count": len(tickets), "tickets": trows})
    return {"config": cfg, "departments": rows}


def handle(args) -> int:
    cd = _company_dir()
    if args.action == "run":
        payload = {"dept": args.dept, "project_root": str(Path(args.project_dir).resolve())}
        if cd: payload["company_dir"] = cd
        res = mcp_client.call_tool("gates_run", payload)
        # T3 (0.19): the renderer IS the face — the raw log lines
        # (ISO timestamps, the strict=... line) stay in the --json
        # envelope, not printed on top of the summary.
        return present.finish(res, args,
                              lambda r: present.render_gates_run(r, args.dept),
                              pass_stderr=False)

    if args.action == "check":
        payload = {"dept": args.dept, "ticket_id": args.ticket_id}
        if cd: payload["company_dir"] = cd
        res = mcp_client.call_tool("gates_check", payload)

        def _check_error(r: dict) -> str:
            # T3 (0.19): the no-report failure — the common case, every
            # ticket before its first gate run — gets a named face (the
            # raw `no gates report found` envelope text was the wire,
            # not the answer; it stays in the --json envelope). Other
            # failures keep their message.
            if "no gates report" in (r.get("stderr") or ""):
                return (f"No gates report for {args.ticket_id} — the ticket "
                        f"is not in-review yet, or its gates have not run; "
                        f"run 'hive gates run {args.dept}'.")
            return (r.get("stderr") or r.get("error") or "unknown error").strip()

        return present.finish(res, args, present.render_gates_check,
                              error=_check_error)

    if args.action == "status":
        company_dir = cd or str(Path.cwd() / ".company")
        depts_dir = Path(company_dir) / "departments"
        if not depts_dir.is_dir():
            msg = f"ERROR: no departments directory at {depts_dir}"
            if getattr(args, "json", False):
                print(present.envelope({"ok": False, "exit_code": 1,
                                        "stderr": msg, "data": None}))
            else:
                print(msg, file=sys.stderr)
            return 1

        data = _collect_gates_status(company_dir)
        if getattr(args, "json", False):
            print(present.envelope({"ok": True, "exit_code": 0,
                                    "stderr": "", "data": data}))
            return 0
        print(present.render_gates_status(data["config"], data["departments"]))
        return 0

    print(f"unknown gates action: {args.action}", file=sys.stderr)
    return 1
