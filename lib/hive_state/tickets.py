"""Typed ticket accessors — the single ticket-mutation code path.

The ticket scripts (``ticket-create/move/approve/bounce/claim/comment``),
the sprint lifecycle, and the MCP tools all mutate tickets through the
functions here. Bash keeps only its process edge (git + gh PR
side-effects); every read-modify-write of a ticket file happens in this
module, under lock, with schema validation.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from hive_state.core import (
    TICKET_PRIORITIES,
    TICKET_STATUSES,
    TICKET_TYPES,
    CompanyError,
    atomic_write,
    company_root,
    locked,
    read_json,
    validate,
)

ACTIVE_STATUSES = ["backlog", "sprint-current", "in-progress", "in-review", "done"]

# ------------------------------------------------------------------ errors


class MoveDenied(CompanyError):
    """A ticket move was rejected by role/gate policy (CLI exit 3)."""


class NotFound(CompanyError):
    """The referenced ticket/file does not exist (CLI exit 5)."""


# ------------------------------------------------------------------ helpers


def _now() -> str:
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ticket_rel(dept: str, status: str, tid: str) -> str:
    return f"departments/{dept}/tickets/{status}/{tid}.json"


def find_ticket(dept: str, tid: str,
                company_dir: str | None = None) -> tuple[str | None, str | None]:
    """Return (status, rel) of the ticket's file, or (None, None)."""
    root = company_root(company_dir)
    for st in TICKET_STATUSES:
        rel = _ticket_rel(dept, st, tid)
        if (root / rel).is_file():
            return st, rel
    return None, None


def _load(dept: str, status: str, tid: str, company_dir: str | None) -> dict:
    rel = _ticket_rel(dept, status, tid)
    data = read_json(rel, company_dir)
    if data is None:
        raise NotFound(f"Ticket {tid} not found in department {dept}")
    return data


def _sprint_stage(company_dir: str | None) -> str:
    st = read_json("sprints/current.json", company_dir, default={})
    return (st or {}).get("stage", "") or ""


def _role_env() -> str:
    return os.environ.get("HIVE_ROLE", "") or ""


def _scripted() -> bool:
    return os.environ.get("HIVE_SCRIPTED_MOVE", "0") == "1"


def _has_real_assignee(ticket: dict) -> bool:
    a = ticket.get("assignee")
    name = a.get("name", "") if isinstance(a, dict) else str(a) if a else ""
    return bool(name) and name not in (
        "Unassigned", "Unassigned - will be claimed by IC", "unassigned", "")


def _merge_comments(ticket: dict, other: dict) -> None:
    """Merge comments from ``other`` into ``ticket`` (dedup, sort by ts)."""
    seen: set = set()
    merged: list = []
    for c in list(other.get("comments") or []) + list(ticket.get("comments") or []):
        if not isinstance(c, dict):
            continue
        key = (c.get("timestamp", ""), c.get("author", ""), (c.get("body", "") or "")[:100])
        if key not in seen:
            seen.add(key)
            merged.append(c)
    merged.sort(key=lambda x: x.get("timestamp", ""))
    ticket["comments"] = merged


def _append_comment(ticket: dict, author: str, ts: str, body: str) -> None:
    if not isinstance(ticket.get("comments"), list):
        ticket["comments"] = []
    ticket["comments"].append({"author": author, "timestamp": ts, "body": body})


def _resolution(ticket: dict) -> dict:
    if not isinstance(ticket.get("resolution"), dict):
        ticket["resolution"] = {}
    return ticket["resolution"]


def _move_file(root: Path, src_rel: str, dst_rel: str, ticket: dict) -> None:
    """Write the moved ticket to dst, delete src + any duplicates.

    All under the destination lock so a concurrent move of the same
    ticket cannot interleave read-modify-write cycles.
    """
    with locked(root, dst_rel):
        validate(dst_rel, ticket)
        dst = root / dst_rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + ".hive.tmp")
        import json
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(ticket, fh, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, dst)
        src = root / src_rel
        if str(src) != str(dst) and src.is_file():
            src.unlink()
    # duplicate cleanup across status dirs (defensive)
    dept = ticket.get("filed_by_department") or ticket.get("target_department", "")
    if dept:
        for st in ACTIVE_STATUSES + ["archive"]:
            dup = root / _ticket_rel(dept, st, ticket["id"])
            if st != ticket["status"] and dup.is_file():
                dup.unlink()


# ------------------------------------------------------------------ create


TYPE_SYNONYMS = {
    "feat": "feature", "features": "feature", "enhancement": "feature", "story": "feature",
    "defect": "bug", "fix": "bug", "hotfix": "bug", "bugfix": "bug",
    "improve": "improvement", "improvements": "improvement", "polish": "improvement",
    "refactor": "improvement",
    "task": "chore", "chores": "chore", "maintenance": "chore",
    "infra": "infrastructure", "ops": "infrastructure", "devops": "infrastructure",
    "infra-task": "infrastructure",
    "investigate": "research", "spike": "research", "study": "research",
}
PRIORITY_SYNONYMS = {
    "p0": "critical", "urgent": "critical", "blocker": "critical", "severe": "critical",
    "p1": "high", "important": "high",
    "p2": "medium", "normal": "medium", "standard": "medium",
    "p3": "low", "nice-to-have": "low", "minor": "low",
}
ESTIMATE_SYNONYMS = {
    "xs": "trivial", "tiny": "trivial",
    "s": "small", "sm": "small",
    "m": "medium", "md": "medium",
    "l": "large", "lg": "large", "big": "large",
    "xl": "xlarge", "xxl": "xlarge", "huge": "xlarge", "epic": "xlarge",
}
VALID_ESTIMATES = ["trivial", "small", "medium", "large", "xlarge"]


def _norm(v: str, table: dict) -> str:
    v = v.strip().lower()
    return table.get(v, v)


def _dept_prefix(dept: str) -> str:
    p = "".join(c for c in dept.upper() if c.isalpha())[:12]
    return p or "TICK"


def resolve_dept(dept: str, company_dir: str | None) -> tuple[str, bool]:
    """Match dept against org-chart (exact, case-insensitive, fuzzy).

    Returns (canonical_dept, corrected). Raises NotFound when org-chart
    exists and has no match.
    """
    oc = read_json("org-chart.json", company_dir, default=None)
    if not isinstance(oc, dict):
        return dept, False
    depts = list((oc.get("departments") or {}).keys())
    if dept in depts:
        return dept, False
    dl = dept.lower()
    for d in depts:
        if d.lower() == dl:
            return d, True
    for d in depts:
        if dl in d.lower() or d.lower() in dl:
            return d, True
    raise NotFound(
        f"Department '{dept}' not found in org-chart.json. Valid departments: "
        + ", ".join(depts))


def _duplicate_title(dept: str, title: str, company_dir: str | None) -> str | None:
    root = company_root(company_dir)
    t = title.strip().lower()
    for st in ACTIVE_STATUSES:
        d = root / "departments" / dept / "tickets" / st
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.json")):
            try:
                data = read_json(str(f.relative_to(root)), company_dir)
                if isinstance(data, dict) and str(data.get("title", "")).strip().lower() == t:
                    return str(data.get("id", ""))
            except CompanyError:
                continue
    return None


def ticket_create(dept: str, type: str, priority: str, title: str, description: str,
                  created_by: str = "user", source: str = "manual",
                  estimate: str = "medium", labels: str = "", ac: str = "",
                  external_id: str = "",
                  company_dir: str | None = None) -> dict:
    """Create a ticket in the dept's backlog. Returns the ticket dict
    (plus meta keys _dept/_corrected)."""
    dept, corrected = resolve_dept(dept, company_dir)
    type = _norm(type, TYPE_SYNONYMS)
    priority = _norm(priority, PRIORITY_SYNONYMS)
    estimate = _norm(estimate, ESTIMATE_SYNONYMS)

    if type not in TICKET_TYPES:
        raise CompanyError(f"type='{type}' is not valid. Must be one of: {' '.join(TICKET_TYPES)}")
    if priority not in TICKET_PRIORITIES:
        raise CompanyError(f"priority='{priority}' is not valid. Must be one of: {' '.join(TICKET_PRIORITIES)}")
    if estimate not in VALID_ESTIMATES:
        estimate = "medium"
    # auto-swap: type looks like a priority and vice versa
    if type in TICKET_PRIORITIES and priority in TICKET_TYPES:
        type, priority = priority, type

    dup = _duplicate_title(dept, title, company_dir)
    if dup:
        return {"duplicate_of": dup, "_dept": dept, "_corrected": corrected}

    root = company_root(company_dir)
    for st in TICKET_STATUSES:
        (root / "departments" / dept / "tickets" / st).mkdir(parents=True, exist_ok=True)

    counter_rel = f"departments/{dept}/tickets/counter.json"
    with locked(root, counter_rel):
        counter = read_json(counter_rel, company_dir, default={}) or {}
        prefix = counter.get("prefix") or ""
        if not re.search(r"[A-Za-z0-9]", prefix):
            prefix = _dept_prefix(dept)
        try:
            next_id = int(counter.get("next_id") or 0)
        except (TypeError, ValueError):
            next_id = 0
        if next_id < 1:
            # corrupted/seeded counter: recover from existing ticket files
            hi = 0
            for st in TICKET_STATUSES:
                d = root / "departments" / dept / "tickets" / st
                if not d.is_dir():
                    continue
                for f in d.glob("*.json"):
                    m = re.search(r"-(\d+)\.json$", f.name)
                    if m:
                        hi = max(hi, int(m.group(1)))
            next_id = hi + 1
        ticket_id = f"{prefix}-{next_id:04d}"
        atomic_write(counter_rel, {"prefix": prefix, "next_id": next_id + 1}, company_dir)

    ts = _now()
    ticket: dict[str, Any] = {
        "id": ticket_id,
        "type": type,
        "title": title,
        "description": description,
        "created_by": created_by,
        "filed_by_department": dept,
        "source": source,
        "priority": priority,
        "status": "backlog",
        "target_department": dept,
        "assignee": {"role": "unassigned", "agent_id": "", "name": "Unassigned"},
        "sprint": None,
        "estimate": estimate,
        "dependencies": [],
        "blocked_by": [],
        "labels": [x.strip() for x in labels.split(",") if x.strip()],
        "acceptance_criteria": [x.strip() for x in ac.split("\n") if x.strip()],
        "comments": [],
        "resolution": {"status": None, "resolved_at": None, "resolved_by": None,
                       "git_branch": None, "commit": None, "notes": None},
        "created_at": ts,
        "updated_at": ts,
        "external_id": external_id or None,
    }
    try:
        atomic_write(_ticket_rel(dept, "backlog", ticket_id), ticket, company_dir)
    except CompanyError:
        # counter already advanced — roll it back and fail loudly
        atomic_write(counter_rel, {"prefix": prefix, "next_id": next_id}, company_dir)
        raise
    ticket["_dept"] = dept
    ticket["_corrected"] = corrected
    return ticket


# ------------------------------------------------------------------ move


def _check_move_policy(dept: str, tid: str, new_status: str, from_status: str,
                       reason: str | None, ticket: dict, company_dir: str | None,
                       same_status: bool = False) -> None:
    """Role + gate policy for a ticket move. Raises MoveDenied."""
    role = _role_env()
    scripted = _scripted()

    if role:
        if new_status == "backlog" and not scripted:
            raise MoveDenied("Demotion to backlog is scripted (sprint-lifecycle defer-on-bounce-limit).")
        if new_status == "sprint-current" and not scripted:
            raise MoveDenied("Promotion to sprint-current is scripted (sprint-fill).")
        if new_status == "in-progress" and not scripted and role != "Verification-IC":
            raise MoveDenied("Only the sprint-lifecycle script or Verification-IC can move to in-progress.")
        if new_status == "in-review" and not scripted and role != "IC":
            raise MoveDenied("Only the sprint-lifecycle or engineering IC can move to in-review.")
        if new_status == "done" and reason == "rejected":
            if not scripted and role not in ("Verification-IC", "VP"):
                raise MoveDenied("Only Verification-ICs, VPs (during their stages), or the system can reject tickets.")
            if role == "VP" and not scripted:
                stage = _sprint_stage(company_dir)
                if stage not in ("vp-plan", "vp-review", "ceo-bugfix-plan", "vp-bugfix-plan"):
                    raise MoveDenied(
                        "VP rejection only allowed during stages vp-plan, vp-review, "
                        f"ceo-bugfix-plan, vp-bugfix-plan (current stage: {stage or 'unknown'}).")
        if new_status == "done" and reason != "rejected" and \
                role != "Verification-IC" and not scripted:
            raise MoveDenied(f"Only Verification-IC can move {tid} to done with reason={reason}.")

    if new_status == "in-progress" and not scripted and role != "Verification-IC":
        raise MoveDenied(
            f"{tid} — agents cannot move tickets directly to in-progress. Use ticket_bounce "
            "(verification IC) or let the sprint-lifecycle script it.")

    # HARD GATE: done requires verification evidence (except rejection/scripted)
    if new_status == "done" and reason != "rejected" and not scripted:
        gates_ok = ticket.get("gates_passed") is True
        verified_by = ticket.get("verified_by") or ""
        verified_at = ticket.get("verified_at") or ""
        if not gates_ok:
            raise MoveDenied(
                f"{tid} cannot move to done — deterministic gates have not passed. "
                "Use ticket_approve (atomic: runs gates, stamps verification, moves to done).")
        if not verified_by or not verified_at:
            raise MoveDenied(
                f"{tid} passed gates but has not been approved by a verification IC. "
                "Use ticket_approve — ONE call, no follow-up.")

    if same_status:
        return  # terminal-state + assignee checks apply to real transitions only

    if from_status in ("done", "archive"):
        raise MoveDenied(f"Cannot move {tid} out of {from_status}. Tickets in done/archive are final.")

    if new_status == "in-review" and not _has_real_assignee(ticket) and not scripted:
        raise MoveDenied(
            f"{tid} — agents do not move tickets to in-review directly (and this ticket has "
            "no assignee). Complete your work and exit cleanly; the sprint-lifecycle moves it.")


def ticket_move(dept: str, tid: str, new_status: str, comment: str = "", author: str = "system",
                reason: str | None = None,
                git_branch: str = "", git_commit: str = "", git_worktree: str = "",
                project_root: str = "",
                company_dir: str | None = None) -> dict:
    """Move a ticket between statuses. Returns {from_status, to_status,
    noop, pr_action}. Raises MoveDenied/NotFound/CompanyError."""
    if new_status not in ACTIVE_STATUSES:
        raise CompanyError(f"Invalid status: {new_status}. Must be one of: {' '.join(ACTIVE_STATUSES)}")

    # Reason validation precedes the lookup (pre-G1 order): a malformed
    # move fails on the argument, not on a confusing not-found.
    if new_status == "done" and not reason:
        raise MoveDenied(
            f"{tid} — moving to done requires a reason (implemented | rejected | done).")
    if reason and reason not in ("implemented", "rejected", "done"):
        raise MoveDenied(f"Invalid reason '{reason}'. Must be one of: implemented, rejected, done")

    from_status, found_rel = find_ticket(dept, tid, company_dir)
    if from_status is None:
        raise NotFound(f"Ticket {tid} not found in department {dept}")
    ticket = _load(dept, from_status, tid, company_dir)

    # Policy first (matches the pre-G1 script order: role/gate denials
    # fire even when the ticket is already at the target status).
    _check_move_policy(dept, tid, new_status, from_status, reason, ticket, company_dir,
                       same_status=(from_status == new_status))

    if from_status == new_status:
        return {"noop": True, "from_status": from_status, "to_status": new_status, "pr_action": "none"}

    ts = _now()
    ticket["status"] = new_status
    ticket["updated_at"] = ts

    if new_status == "done":
        comment_text = comment or "Marked as done"
        prefix = "REJECTED" if reason == "rejected" else "COMPLETED"
        _append_comment(ticket, author, ts, prefix + ": " + comment_text)
        ticket.pop("skip_tdd_red", None)
        res = _resolution(ticket)
        res["status"] = reason
        res["resolved_at"] = ts
        res["resolved_by"] = author
        if git_branch:
            res["git_branch"] = git_branch
        if git_commit:
            res["commit"] = git_commit
        if git_worktree:
            res["worktree"] = git_worktree
    elif new_status == "in-progress":
        res = _resolution(ticket)
        res.setdefault("status", None)
        res.setdefault("resolved_at", None)
        res.setdefault("resolved_by", None)
        res["git_branch"] = "feature/" + tid
        res["worktree"] = (project_root.rstrip("/") + "/.worktrees/" + tid) if project_root else ""
        if not res.get("started_at"):
            res["started_at"] = ts
        if git_commit:
            res["commit"] = git_commit
        if comment:
            _append_comment(ticket, author, ts, comment)

    root = company_root(company_dir)
    dst_rel = _ticket_rel(dept, new_status, tid)
    # merge comments from a pre-existing destination copy (race protection)
    if found_rel != dst_rel:
        existing = read_json(dst_rel, company_dir)
        if isinstance(existing, dict):
            _merge_comments(ticket, existing)

    _move_file(root, found_rel, dst_rel, ticket)

    pr_action = "none"
    if new_status == "in-review":
        pr_action = "open"
    elif new_status == "done":
        pr_action = "close" if reason == "rejected" else "merge"
    return {
        "noop": False,
        "from_status": from_status,
        "to_status": new_status,
        "pr_action": pr_action,
        "ticket_id": tid,
        "dept": dept,
    }


def ticket_rollback_conflict(dept: str, tid: str,
                             company_dir: str | None = None) -> dict:
    """PR-merge-conflict rollback: done -> in-progress, skip_tdd_red=true,
    verification fields cleared, rejection comment recorded."""
    from_status, _ = find_ticket(dept, tid, company_dir)
    if from_status is None:
        raise NotFound(f"Ticket {tid} not found in department {dept}")
    ticket = _load(dept, from_status, tid, company_dir)
    ts = _now()
    ticket["status"] = "in-progress"
    ticket["updated_at"] = ts
    ticket["skip_tdd_red"] = True
    ticket["verified_by"] = ""
    ticket["verified_at"] = ""
    ticket["gates_passed"] = False
    _append_comment(
        ticket, "ticket-move", ts,
        "REJECTED by verification: PR merge conflict on approve. Bounced to in-progress "
        "with skip_tdd_red=true. Engineering IC: rebase feature branch on main, resolve "
        "conflicts, force-push, then proceed normally. PR remains open.")
    ticket["resolution"] = None
    root = company_root(company_dir)
    dst_rel = _ticket_rel(dept, "in-progress", tid)
    _move_file(root, _ticket_rel(dept, from_status, tid), dst_rel, ticket)
    return {"from_status": from_status, "to_status": "in-progress", "ticket_id": tid}


# ------------------------------------------------------------------ approve / bounce


def ticket_approve(dept: str, tid: str, verifier: str, rationale: str,
                   company_dir: str | None = None) -> dict:
    """Atomic approval: stamp verification + move to done (reason=implemented).

    Returns the move result; the PR-merge side-effect is a process edge
    owned by the caller (bash wrapper / MCP layer).
    """
    if len(rationale) < 30:
        raise CompanyError(
            f"rationale too short ({len(rationale)} chars, need >=30). "
            "Rubber-stamp approvals are not allowed. Describe what you actually checked.")
    status, _ = find_ticket(dept, tid, company_dir)
    if status is None:
        raise NotFound(f"Ticket {tid} not found in department {dept}")
    if status != "in-review":
        raise CompanyError(f"ticket {tid} is not in in-review for {dept} (cannot approve)")
    rel = _ticket_rel(dept, "in-review", tid)
    with locked(company_root(company_dir), rel):
        ticket = read_json(rel, company_dir)
        if ticket.get("gates_passed") is not True:
            raise CompanyError(
                f"deterministic gates have not passed for {tid}. "
                "Run verification-gates.sh; if gates fail the ticket will be bounced.")
        ts = _now()
        ticket["verified_by"] = verifier
        ticket["verified_at"] = ts
        ticket["verification_rationale"] = rationale
        validate(rel, ticket)
        import json
        p = company_root(company_dir) / rel
        tmp = p.with_name(p.name + ".hive.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(ticket, fh, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
    ticket_comment(dept, tid, verifier, f"✅ APPROVED. Rationale: {rationale}", company_dir)
    os.environ["HIVE_SCRIPTED_MOVE"] = "1"
    os.environ["HIVE_ROLE"] = "Verification-IC"
    try:
        short = " ".join(rationale[:200].splitlines())
        result = ticket_move(
            dept, tid, "done",
            comment=f"Verified by {verifier}: {short}",
            author=verifier, reason="implemented",
            company_dir=company_dir)
    finally:
        os.environ.pop("HIVE_SCRIPTED_MOVE", None)
        os.environ.pop("HIVE_ROLE", None)
    result["verifier"] = verifier
    return result


def ticket_bounce(dept: str, tid: str, author: str, rationale: str,
                  skip_tdd_red: bool = False,
                  company_dir: str | None = None) -> dict:
    """Atomic bounce: in-review -> in-progress with full rationale comment,
    bounce_count incremented, optional skip_tdd_red flag."""
    if len(rationale) < 80:
        raise CompanyError(
            f"rationale must be >=80 chars (got {len(rationale)}). A bounce must enumerate the actual problems.")
    status, _ = find_ticket(dept, tid, company_dir)
    if status is None:
        raise NotFound(f"Ticket {tid} not found in department {dept}")
    rel = _ticket_rel(dept, status, tid)
    with locked(company_root(company_dir), rel):
        ticket = read_json(rel, company_dir)
        if skip_tdd_red:
            ticket["skip_tdd_red"] = True
        ticket["bounce_count"] = int(ticket.get("bounce_count") or 0) + 1
        validate(rel, ticket)
        import json
        p = company_root(company_dir) / rel
        tmp = p.with_name(p.name + ".hive.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(ticket, fh, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
    os.environ["HIVE_SCRIPTED_MOVE"] = "1"
    os.environ["HIVE_ROLE"] = "Verification-IC"
    try:
        result = ticket_move(
            dept, tid, "in-progress",
            comment=f"REJECTED by verification: {rationale}",
            author=author,
            company_dir=company_dir)
    finally:
        os.environ.pop("HIVE_SCRIPTED_MOVE", None)
        os.environ.pop("HIVE_ROLE", None)
    result["skip_tdd_red"] = skip_tdd_red
    return result


# ------------------------------------------------------------------ claim / comment


def ticket_claim(dept: str, tid: str, role: str, agent_id: str, agent_name: str,
                 worktree_path: str = "", project_root: str = "",
                 company_dir: str | None = None) -> dict:
    """Claim a ticket: set assignee, link git branch, move to in-progress."""
    from_status, found_rel = find_ticket(dept, tid, company_dir)
    if from_status is None:
        raise NotFound(f"Ticket {tid} not found in department {dept}")
    if from_status in ("done", "archive"):
        raise MoveDenied(f"Cannot claim {tid} - it is already in {from_status}.")
    ticket = _load(dept, from_status, tid, company_dir)
    ts = _now()
    root = company_root(company_dir)
    worktree = worktree_path
    if not worktree and project_root and (root.parent / ".worktrees" / tid).is_dir():
        worktree = str(root.parent / ".worktrees" / tid)
    ticket["assignee"] = {"role": role, "agent_id": agent_id, "name": agent_name}
    ticket["status"] = "in-progress"
    ticket["updated_at"] = ts
    res = _resolution(ticket)
    res["git_branch"] = "feature/" + tid
    if worktree:
        res["worktree"] = worktree
    res["started_at"] = ts
    dst_rel = _ticket_rel(dept, "in-progress", tid)
    if found_rel != dst_rel:
        existing = read_json(dst_rel, company_dir)
        if isinstance(existing, dict):
            _merge_comments(ticket, existing)
    _move_file(root, found_rel, dst_rel, ticket)
    return {"from_status": from_status, "to_status": "in-progress", "ticket_id": tid}


def ticket_comment(dept: str, tid: str, author: str, text: str,
                   company_dir: str | None = None) -> dict:
    """Append a comment to the ticket (any active status)."""
    status, _ = find_ticket(dept, tid, company_dir)
    if status is None:
        raise NotFound(f"Ticket {tid} not found in department {dept}")
    rel = _ticket_rel(dept, status, tid)
    with locked(company_root(company_dir), rel):
        ticket = read_json(rel, company_dir)
        ts = _now()
        _append_comment(ticket, author, ts, text)
        ticket["updated_at"] = ts
        validate(rel, ticket)
        import json
        p = company_root(company_dir) / rel
        tmp = p.with_name(p.name + ".hive.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(ticket, fh, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
    return {"ticket_id": tid, "status": status}


# ------------------------------------------------------------------ archive


def ticket_archive(sprint_id: str = "unknown",
                   company_dir: str | None = None) -> dict:
    """Sweep every dept's done/*.json into archive/, stamping provenance."""
    root = company_root(company_dir)
    depts_root = root / "departments"
    if not depts_root.is_dir():
        return {"moved": 0, "depts": []}
    ts = _now()
    moved = 0
    depts = []
    for dept_dir in sorted(depts_root.iterdir()):
        done_dir = dept_dir / "tickets" / "done"
        if not done_dir.is_dir():
            continue
        archive_dir = dept_dir / "tickets" / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        dept_moved = 0
        for tf in sorted(done_dir.glob("*.json")):
            rel_src = str(tf.relative_to(root))
            data = read_json(rel_src, company_dir)
            if not isinstance(data, dict):
                continue
            dest = archive_dir / tf.name
            if dest.is_file():
                # archive copy is authoritative; drop the stray done dup
                try:
                    tf.unlink()
                except OSError:
                    pass
                continue
            # Provenance stamp only: the status field is left as-is (the
            # pre-G1 sweep never rewrote it — archived tickets keep their
            # terminal status, the DIRECTORY is the archive marker).
            data["archived_at"] = ts
            data["archived_in_sprint"] = sprint_id
            rel_dst = str(dest.relative_to(root))
            atomic_write(rel_dst, data, company_dir)
            tf.unlink()
            moved += 1
            dept_moved += 1
        if dept_moved:
            depts.append(dept_dir.name)
    return {"moved": moved, "depts": depts}


def ticket_get(dept: str, tid: str, company_dir: str | None = None) -> dict:
    status, _ = find_ticket(dept, tid, company_dir)
    if status is None:
        raise NotFound(f"Ticket {tid} not found in department {dept}")
    ticket = _load(dept, status, tid, company_dir)
    ticket["_status"] = status
    return ticket
