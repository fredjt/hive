"""hive_state CLI — the bash side's single door into .company/ JSON.

    python -m hive_state [--company-dir DIR] <op> [args]

Every op prints exactly one JSON object on stdout:
    {"ok": true, ...result}      on success
    {"ok": false, "error": "..."} on failure
Exit codes: 0 ok · 1 error · 3 policy denial · 4 schema violation · 5 not found.
Policy-denial messages are ALSO written to stderr (agent-facing UX).
"""

from __future__ import annotations

import argparse
import json
import sys

from hive_state import (
    CompanyError,
    MoveDenied,
    NotFound,
    SchemaViolation,
    apply_jq,
    atomic_mkdirs,
    atomic_remove,
    atomic_rename,
    atomic_write,
    read_json,
    round_append,
    round_archive,
    round_close,
    round_get,
    ticket_approve,
    ticket_archive,
    ticket_bounce,
    ticket_claim,
    ticket_comment,
    ticket_create,
    ticket_get,
    ticket_move,
    ticket_rollback_conflict,
)

EXIT = {"ok": 0, "error": 1, "denied": 3, "schema": 4, "notfound": 5}


def _emit(ok: bool, payload: dict, code: int = 0, stderr_msg: str = "") -> int:
    out = {"ok": ok}
    out.update(payload)
    print(json.dumps(out, indent=2))
    if stderr_msg:
        print(stderr_msg, file=sys.stderr)
    return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hive_state", description=__doc__)
    parser.add_argument("--company-dir", default=None,
                        help=".company/ directory (default: $COMPANY_DIR or ./company)")
    sub = parser.add_subparsers(dest="op", required=True)

    p = sub.add_parser("read"); p.add_argument("rel")
    p = sub.add_parser("write"); p.add_argument("rel")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--json"); g.add_argument("--stdin", action="store_true")
    p = sub.add_parser("jq")
    p.add_argument("rel"); p.add_argument("prog")
    p.add_argument("--arg", action="append", nargs=2, default=[])
    p.add_argument("--argjson", action="append", nargs=2, default=[])
    p.add_argument("--no-require", action="store_true")
    p = sub.add_parser("mv"); p.add_argument("src"); p.add_argument("dst")
    p = sub.add_parser("rm"); p.add_argument("rel")
    p = sub.add_parser("mkdirs"); p.add_argument("rel")

    p = sub.add_parser("ticket-create")
    for f, d in (("dept", None), ("type", None), ("priority", None), ("title", None),
                 ("desc", None), ("created-by", "user"), ("source", "manual"),
                 ("estimate", "medium"), ("labels", ""), ("ac", ""), ("external-id", "")):
        p.add_argument("--" + f, dest=f.replace("-", "_"), default=d)
    p = sub.add_parser("ticket-get")
    p.add_argument("--dept"); p.add_argument("--ticket")
    p = sub.add_parser("ticket-move")
    p.add_argument("--dept"); p.add_argument("--ticket"); p.add_argument("--status")
    p.add_argument("--comment", default=""); p.add_argument("--author", default="system")
    p.add_argument("--reason", default=None)
    p.add_argument("--git-branch", default=""); p.add_argument("--git-commit", default="")
    p.add_argument("--git-worktree", default=""); p.add_argument("--project-root", default="")
    p = sub.add_parser("ticket-approve")
    p.add_argument("--dept"); p.add_argument("--ticket")
    p.add_argument("--verifier"); p.add_argument("--rationale")
    p = sub.add_parser("ticket-bounce")
    p.add_argument("--dept"); p.add_argument("--ticket")
    p.add_argument("--author"); p.add_argument("--rationale")
    p.add_argument("--skip-tdd-red", action="store_true")
    p = sub.add_parser("ticket-claim")
    p.add_argument("--dept"); p.add_argument("--ticket")
    p.add_argument("--role"); p.add_argument("--agent-id", default="")
    p.add_argument("--agent-name"); p.add_argument("--worktree-path", default="")
    p.add_argument("--project-root", default="")
    p = sub.add_parser("ticket-comment")
    p.add_argument("--dept"); p.add_argument("--ticket")
    p.add_argument("--author"); p.add_argument("--text")
    p = sub.add_parser("ticket-archive"); p.add_argument("--sprint", default="unknown")
    p = sub.add_parser("ticket-rollback-conflict")
    p.add_argument("--dept"); p.add_argument("--ticket")

    p = sub.add_parser("round-append")
    p.add_argument("--stage"); p.add_argument("--agent"); p.add_argument("--dept")
    p.add_argument("--role"); p.add_argument("--ticket", default="")
    p.add_argument("--status", default="running")
    p = sub.add_parser("jsonl-append")
    p.add_argument("rel"); p.add_argument("--line", default="")
    p.add_argument("--keep", type=int, default=None)
    sub.add_parser("round-close")
    sub.add_parser("round-archive")
    sub.add_parser("round-get")

    a = parser.parse_args(argv)
    cd = a.company_dir

    try:
        if a.op == "read":
            data = read_json(a.rel, cd)
            if data is None:
                return _emit(False, {"error": f"not found: {a.rel}"}, EXIT["notfound"])
            print(json.dumps(data, indent=2))
            return 0

        if a.op == "write":
            if a.stdin:
                data = json.loads(sys.stdin.read())
            else:
                data = json.loads(a.json)
            atomic_write(a.rel, data, cd)
            return _emit(True, {"rel": a.rel})

        if a.op == "jq":
            args: list[str] = []
            for k, v in a.arg:
                args += ["--arg", k, v]
            for k, v in a.argjson:
                args += ["--argjson", k, v]
            applied = apply_jq(a.rel, a.prog, args, cd, require_file=not a.no_require)
            return _emit(True, {"applied": applied})

        if a.op == "mv":
            atomic_rename(a.src, a.dst, cd)
            return _emit(True, {"src": a.src, "dst": a.dst})

        if a.op == "rm":
            existed = atomic_remove(a.rel, cd)
            return _emit(True, {"removed": existed})

        if a.op == "mkdirs":
            atomic_mkdirs(a.rel, cd)
            return _emit(True, {"rel": a.rel})

        if a.op == "jsonl-append":
            from hive_state.core import jsonl_append
            n = jsonl_append(a.rel, a.line, a.keep, cd)
            return _emit(True, {"lines": n})

        if a.op == "ticket-create":
            result = ticket_create(
                a.dept, a.type, a.priority, a.title, a.desc,
                created_by=a.created_by, source=a.source, estimate=a.estimate,
                labels=a.labels, ac=a.ac, external_id=a.external_id, company_dir=cd)
            if "duplicate_of" in result:
                return _emit(True, {"duplicate_of": result["duplicate_of"], "dept": result["_dept"]})
            if result.get("_corrected"):
                print(f"CORRECTED: dept -> {result['_dept']}", file=sys.stderr)
            return _emit(True, {"ticket_id": result["id"], "dept": result["_dept"]})

        if a.op == "ticket-get":
            t = ticket_get(a.dept, a.ticket, cd)
            print(json.dumps(t, indent=2))
            return 0

        if a.op == "ticket-move":
            result = ticket_move(
                a.dept, a.ticket, a.status, comment=a.comment, author=a.author,
                reason=a.reason, git_branch=a.git_branch, git_commit=a.git_commit,
                git_worktree=a.git_worktree, project_root=a.project_root, company_dir=cd)
            return _emit(True, result)

        if a.op == "ticket-approve":
            result = ticket_approve(a.dept, a.ticket, a.verifier, a.rationale, cd)
            return _emit(True, result)

        if a.op == "ticket-bounce":
            result = ticket_bounce(a.dept, a.ticket, a.author, a.rationale,
                                   skip_tdd_red=a.skip_tdd_red, company_dir=cd)
            return _emit(True, result)

        if a.op == "ticket-claim":
            result = ticket_claim(a.dept, a.ticket, a.role, a.agent_id, a.agent_name,
                                  worktree_path=a.worktree_path,
                                  project_root=a.project_root, company_dir=cd)
            return _emit(True, result)

        if a.op == "ticket-comment":
            result = ticket_comment(a.dept, a.ticket, a.author, a.text, cd)
            return _emit(True, result)

        if a.op == "ticket-archive":
            result = ticket_archive(a.sprint, cd)
            return _emit(True, result)

        if a.op == "ticket-rollback-conflict":
            result = ticket_rollback_conflict(a.dept, a.ticket, cd)
            return _emit(True, result)

        if a.op == "round-append":
            entry = round_append(a.stage, a.agent, a.dept, a.role,
                                         a.ticket, a.status, cd)
            return _emit(True, entry)

        if a.op == "round-close":
            round_close(cd)
            return _emit(True, {"closed": True})

        if a.op == "round-archive":
            archived = round_archive(cd)
            return _emit(True, {"archived": archived})

        if a.op == "round-get":
            data = round_get(cd)
            if data is None:
                return _emit(False, {"error": "no current round"}, EXIT["notfound"])
            print(json.dumps(data, indent=2))
            return 0

        parser.error(f"unknown op: {a.op}")
        return 2

    except MoveDenied as exc:
        return _emit(False, {"error": str(exc)}, EXIT["denied"], f"ERROR: ticket-move denied: {exc}")
    except NotFound as exc:
        return _emit(False, {"error": str(exc)}, EXIT["notfound"], str(exc))
    except SchemaViolation as exc:
        return _emit(False, {"error": str(exc)}, EXIT["schema"], f"ERROR: schema violation: {exc}")
    except CompanyError as exc:
        return _emit(False, {"error": str(exc)}, EXIT["error"], f"ERROR: {exc}")
    except json.JSONDecodeError as exc:
        return _emit(False, {"error": f"invalid JSON: {exc}"}, EXIT["error"], f"ERROR: invalid JSON: {exc}")


if __name__ == "__main__":
    sys.exit(main())
