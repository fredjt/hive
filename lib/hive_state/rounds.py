"""Round activity log accessors.

``rounds/current-round.json`` is the sprint lifecycle's per-round activity
log: one entry per agent spawn, entries flipped in place when the spawn
finishes, the whole log archived with the sprint. Written by exactly one
code path: :func:`round_append` / :func:`round_archive` (the lifecycle,
via ``hs round-append`` / ``hs round-archive``). The legacy queue/promote
architecture (round-enqueue/round-promote/round-status) is retired.

File schema (read by the dashboard round panel)::

    {
      "round_id": "sprint-...", "source": "sprint-lifecycle",
      "started_at": "<ts>", "ended_at": null | "<ts>", "cursor": 0,
      "entries": [
        {"id": "<kind>-<ticket|-agent>-<n>", "agent": "ic-tester",
         "dept": "backend", "role": "IC-Tester", "kind": "tdd-red",
         "ticket": "TICK-B-0001", "log_to": "",
         "queued_by": "sprint-lifecycle", "queued_at": "<ts>",
         "status": "running|done|failed|skipped", "started_at": "<ts>",
         "ended_at": null, "spawn_id": null, "exit_code": null,
         "duration_s": null, "skip_reason": null}
      ]
    }
"""

from __future__ import annotations

import datetime

from hive_state.core import (
    CompanyError,
    atomic_rename,
    company_root,
    locked,
    read_json,
    validate,
)

ROUND_REL = "rounds/current-round.json"
HISTORY_DIR = "rounds/history"


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sprint_id(company_dir: str | None) -> str:
    st = read_json("sprints/current.json", company_dir, default={})
    sid = (st or {}).get("sprint_id")
    return str(sid) if sid else "unknown"


def round_get(company_dir: str | None = None) -> dict | None:
    return read_json(ROUND_REL, company_dir)


def _ensure(data: dict | None, company_dir: str | None) -> dict:
    if isinstance(data, dict) and isinstance(data.get("entries"), list):
        return data
    return {
        "round_id": _sprint_id(company_dir),
        "source": "sprint-lifecycle",
        "started_at": _now(),
        "ended_at": None,
        "cursor": 0,
        "entries": [],
    }


def _write(root, rel: str, data: dict) -> None:
    validate(rel, data)
    from pathlib import Path
    import json
    import os
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".hive.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, p)


def round_append(stage: str, agent: str, dept: str, role: str,
                 ticket: str, status: str,
                 company_dir: str | None = None) -> dict:
    """Append (or complete) one round entry; returns the entry.

    status="running" appends a new entry; done/failed/skipped flips the
    most recent still-running entry with the same kind+ticket (matching
    the pre-G1 jq search), falling back to an append so nothing is lost.
    """
    if status not in ("running", "done", "failed", "skipped"):
        raise CompanyError(f"invalid round status: {status!r}")
    root = company_root(company_dir)
    with locked(root, ROUND_REL):
        data = _ensure(read_json(ROUND_REL, company_dir), company_dir)
        entries = data["entries"]
        now = _now()
        if status == "running":
            label = ticket if ticket else agent
            entry = {
                "id": f"{stage}-{label}-{len(entries) + 1}",
                "agent": agent, "dept": dept, "role": role, "kind": stage,
                "ticket": ticket if ticket else None,
                "log_to": "", "queued_by": "sprint-lifecycle",
                "queued_at": now, "status": "running",
                "started_at": now, "ended_at": None,
                "spawn_id": None, "exit_code": None,
                "duration_s": None, "skip_reason": None,
            }
            entries.append(entry)
        else:
            idx = -1
            for i in range(len(entries) - 1, -1, -1):
                e = entries[i]
                if (e.get("kind") == stage
                        and (e.get("ticket") or "") == (ticket or "")
                        and e.get("status") == "running"):
                    idx = i
                    break
            if idx >= 0:
                entries[idx]["status"] = status
                entries[idx]["ended_at"] = now
                entry = entries[idx]
            else:
                label = ticket if ticket else agent
                entry = {
                    "id": f"{stage}-{label}-{len(entries) + 1}",
                    "agent": agent, "dept": dept, "role": role, "kind": stage,
                    "ticket": ticket if ticket else None,
                    "log_to": "", "queued_by": "sprint-lifecycle",
                    "queued_at": now, "status": status,
                    "started_at": now, "ended_at": now,
                    "spawn_id": None, "exit_code": None,
                    "duration_s": None, "skip_reason": None,
                }
                entries.append(entry)
        _write(root, ROUND_REL, data)
    return entry


def round_close(company_dir: str | None = None) -> None:
    """Stamp ended_at (idempotent no-op when no round file exists)."""
    root = company_root(company_dir)
    with locked(root, ROUND_REL):
        data = read_json(ROUND_REL, company_dir)
        if not isinstance(data, dict):
            return
        data["ended_at"] = _now()
        _write(root, ROUND_REL, data)


def round_archive(company_dir: str | None = None) -> bool:
    """Stamp ended_at + move the log to rounds/history/ (the sprint's end)."""
    root = company_root(company_dir)
    if not (root / ROUND_REL).is_file():
        return False
    round_close(company_dir)
    atomic_rename(ROUND_REL, HISTORY_DIR + "/current-round.json", company_dir,
                  validate_dst=False)
    return True
