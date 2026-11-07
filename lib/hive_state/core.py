"""Core primitives of the .company/ state layer.

One atomic-write convention for the whole runtime:

    <file>.hive.tmp  ->  fsync  ->  os.replace(<file>)

taken under an exclusive flock on a sidecar lock file
(``.company/.locks/<flat-rel-path>.lock``). Every mutation in the tree
goes through :func:`atomic_write` / :func:`apply_jq`; nothing else may
touch .company/ JSON.
"""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

__all__ = [
    "SCHEMAS",
    "CompanyError",
    "SchemaViolation",
    "apply_jq",
    "atomic_mkdirs",
    "atomic_remove",
    "atomic_rename",
    "atomic_write",
    "company_dir",
    "company_root",
    "locked",
    "read_json",
    "register_schema",
    "validate",
]

TMP_SUFFIX = ".hive.tmp"

# ---------------------------------------------------------------- errors


class CompanyError(Exception):
    """Base error: a .company/ state operation failed."""


class SchemaViolation(CompanyError):
    """A write would have produced data violating the registered schema."""


# ---------------------------------------------------------------- paths


def company_root(company_dir: str | os.PathLike | None = None) -> Path:
    """Resolve the .company/ root (never its parent)."""
    root = Path(company_dir or os.environ.get("COMPANY_DIR", ".company")).expanduser()
    if not root.is_absolute():
        root = Path.cwd() / root
    return root


def company_dir(company_dir: str | os.PathLike | None = None) -> Path:
    """Alias kept for call-site clarity: the .company/ directory itself."""
    return company_root(company_dir)


def _safe_rel(rel: str) -> str:
    rel = str(rel).lstrip("/")
    if not rel or rel.startswith("..") or "/../" in rel:
        raise CompanyError(f"unsafe relative path: {rel!r}")
    return rel


def lock_path(root: Path, rel: str) -> Path:
    flat = _safe_rel(rel).replace("/", "__")
    return root / ".locks" / (flat + ".lock")


_LOCK_HELD = threading.local()  # per-thread {lockpath: count}


@contextmanager
def locked(root: Path, rel: str, timeout: float = 30.0) -> Iterator[None]:
    """Exclusive per-file lock (flock) for mutations of ``rel``.

    Lock files live in ``.company/.locks/`` so they never sit beside the
    data files (a rebuild that globs a data directory can't sweep them).

    Re-entrancy: a nested acquisition of the SAME path from the SAME
    thread is a no-op (the outer flock is already held) — accessors
    compose freely (e.g. ticket_create holds the counter lock while
    atomic_write re-locks the counter file). A different thread still
    contends on the flock, as it must.
    """
    lp = lock_path(root, rel)
    lp.parent.mkdir(parents=True, exist_ok=True)
    held = getattr(_LOCK_HELD, "held", None)
    if held is None:
        held = {}
        _LOCK_HELD.held = held
    key = str(lp)
    if held.get(key, 0) > 0:
        held[key] += 1
        try:
            yield
        finally:
            held[key] -= 1
        return
    fd = os.open(str(lp), os.O_RDWR | os.O_CREAT, 0o644)
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (BlockingIOError, OSError):
                if time.monotonic() >= deadline:
                    raise CompanyError(
                        f"timed out after {timeout}s waiting for lock on {rel}")
                time.sleep(0.05)
        held[key] = 1
        yield
    finally:
        held[key] = 0
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


# ---------------------------------------------------------------- read


def read_json(rel: str, company_dir: str | os.PathLike | None = None,
              default: Any = None) -> Any:
    """Read a .company/ JSON file; return ``default`` when absent."""
    root = company_root(company_dir)
    p = root / _safe_rel(rel)
    if not p.is_file():
        return default
    with open(p, "r", encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------- schemas

# rel-path patterns (fnmatch) -> validator(data, rel) that raises
# SchemaViolation. Registration order matters: first match wins.
from fnmatch import fnmatch

SCHEMAS: dict[str, Any] = {}


def register_schema(pattern: str, validator) -> None:
    SCHEMAS[pattern] = validator


def validate(rel: str, data: Any) -> None:
    """Validate ``data`` against the schema registered for ``rel``.

    No registered schema means no validation (the file may still be
    written). A violation raises SchemaViolation BEFORE anything is
    written to disk.
    """
    rel = _safe_rel(rel)
    for pattern, validator in SCHEMAS.items():
        if fnmatch(rel, pattern):
            validator(data, rel)
            return


def _require_dict(data: Any, rel: str) -> dict:
    if not isinstance(data, dict):
        raise SchemaViolation(f"{rel}: expected a JSON object, got {type(data).__name__}")
    return data


def _require_str(data: dict, key: str, rel: str) -> str:
    v = data.get(key)
    if not isinstance(v, str):
        raise SchemaViolation(f"{rel}: missing string field {key!r}")
    return v


# --- ticket files: departments/<dept>/tickets/<status>/<id>.json ------

TICKET_STATUSES = ["backlog", "sprint-current", "in-progress", "in-review", "done", "archive"]
TICKET_TYPES = ["feature", "bug", "improvement", "chore", "infrastructure", "research"]
TICKET_PRIORITIES = ["critical", "high", "medium", "low"]


def _validate_ticket(data: Any, rel: str) -> None:
    t = _require_dict(data, rel)
    # created_at is NOT required: hand-seeded test fixtures are minimal but
    # legal — the state layer validates structure, not provenance.
    for key in ("id", "title", "type", "priority", "status"):
        if key not in t or not isinstance(t[key], str) or not t[key]:
            raise SchemaViolation(f"{rel}: ticket missing required string field {key!r}")
    parts = rel.split("/")
    if len(parts) == 5 and parts[0] == "departments" and parts[2] == "tickets":
        dir_status = parts[3]
        if dir_status not in TICKET_STATUSES:
            raise SchemaViolation(f"{rel}: unknown status directory {dir_status!r}")
        if dir_status != "archive" and t["status"] != dir_status:
            # archive/ holds terminal tickets whose status field is the
            # status they reached (done/rejected) — the directory is the
            # archive marker, not a status value.
            raise SchemaViolation(
                f"{rel}: ticket status {t['status']!r} does not match directory {dir_status!r}")
    if t["type"] not in TICKET_TYPES:
        raise SchemaViolation(f"{rel}: ticket type {t['type']!r} not in {TICKET_TYPES}")
    if t["priority"] not in TICKET_PRIORITIES:
        raise SchemaViolation(f"{rel}: ticket priority {t['priority']!r} not in {TICKET_PRIORITIES}")


def _validate_round(data: Any, rel: str) -> None:
    r = _require_dict(data, rel)
    for key in ("round_id", "source", "started_at", "entries"):
        if key not in r:
            raise SchemaViolation(f"{rel}: round file missing field {key!r}")
    if not isinstance(r["entries"], list):
        raise SchemaViolation(f"{rel}: entries must be a list")
    for i, e in enumerate(r["entries"]):
        if not isinstance(e, dict):
            raise SchemaViolation(f"{rel}: entry {i} is not an object")
        for key in ("id", "agent", "kind", "status", "started_at"):
            if key not in e:
                raise SchemaViolation(f"{rel}: entry {i} missing field {key!r}")
        if e["status"] not in ("running", "done", "failed", "skipped"):
            raise SchemaViolation(f"{rel}: entry {i} status must be one of running/done/failed/skipped")


def _validate_counter(data: Any, rel: str) -> None:
    c = _require_dict(data, rel)
    if not isinstance(c.get("next_id"), int) or c["next_id"] < 0:
        raise SchemaViolation(f"{rel}: counter must have a non-negative int 'next_id'")
    if not isinstance(c.get("prefix"), str):
        raise SchemaViolation(f"{rel}: counter must have a string 'prefix'")


def _validate_manifest(data: Any, rel: str) -> None:
    m = _require_dict(data, rel)
    if not isinstance(m.get("project_name"), str):
        raise SchemaViolation(f"{rel}: manifest missing string 'project_name'")


def _validate_sprint_state(data: Any, rel: str) -> None:
    s = _require_dict(data, rel)
    if not isinstance(s.get("stage"), str):
        raise SchemaViolation(f"{rel}: sprint state missing string 'stage'")


register_schema("departments/*/tickets/*/*.json", _validate_ticket)
register_schema("rounds/*.json", _validate_round)
register_schema("departments/*/tickets/counter.json", _validate_counter)
register_schema("manifest.json", _validate_manifest)
register_schema("sprints/current.json", _validate_sprint_state)


# ---------------------------------------------------------------- write


def atomic_write(rel: str, data: Any,
                 company_dir: str | os.PathLike | None = None) -> None:
    """Validate then atomically write ``data`` to ``rel`` under lock."""
    root = company_root(company_dir)
    rel = _safe_rel(rel)
    p = root / rel
    with locked(root, rel):
        validate(rel, data)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + TMP_SUFFIX)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)


def apply_jq(rel: str, program: str,
             args: list[str] | None = None,
             company_dir: str | os.PathLike | None = None,
             require_file: bool = True) -> bool:
    """Atomically apply a jq program to ``rel`` under lock.

    The jq output must be valid JSON; it is schema-validated BEFORE the
    rename. Returns True when a write happened, False when the file was
    absent and ``require_file`` is false (no-op). Raises CompanyError on
    jq failure or validation failure — the file is left untouched.
    """
    root = company_root(company_dir)
    rel = _safe_rel(rel)
    p = root / rel
    if not p.is_file():
        if require_file:
            raise CompanyError(f"file not found: {rel}")
        return False
    if args is None:
        args = []
    with locked(root, rel):
        if not p.is_file():
            if require_file:
                raise CompanyError(f"file not found: {rel}")
            return False
        cmd = ["jq", *args, program, str(p)]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30,
                              check=False)
        if proc.returncode != 0:
            raise CompanyError(f"jq failed on {rel}: {proc.stderr.strip() or program}")
        if not proc.stdout.strip():
            raise CompanyError(f"jq produced no output for {rel} (program: {program})")
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise CompanyError(f"jq output for {rel} is not valid JSON: {exc}") from exc
        validate(rel, data)
        tmp = p.with_name(p.name + TMP_SUFFIX)
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(proc.stdout)
            if not proc.stdout.endswith("\n"):
                fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
    return True


# ---------------------------------------------------------------- fs ops


def atomic_rename(src_rel: str, dst_rel: str,
                  company_dir: str | os.PathLike | None = None,
                  validate_dst: bool = True) -> None:
    """Atomically move a file inside .company/ (same filesystem)."""
    root = company_root(company_dir)
    src = root / _safe_rel(src_rel)
    dst = root / _safe_rel(dst_rel)
    if not src.is_file():
        raise CompanyError(f"file not found: {src_rel}")
    if validate_dst and dst.suffix == ".json":
        with open(src, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        validate(dst_rel, data)
    with locked(root, dst_rel):
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.replace(src, dst)


def atomic_remove(rel: str, company_dir: str | os.PathLike | None = None) -> bool:
    """Remove a file inside .company/ under lock; True if it existed."""
    root = company_root(company_dir)
    p = root / _safe_rel(rel)
    with locked(root, rel):
        try:
            p.unlink()
            return True
        except FileNotFoundError:
            return False


def atomic_mkdirs(rel: str, company_dir: str | os.PathLike | None = None) -> None:
    root = company_root(company_dir)
    (root / _safe_rel(rel)).mkdir(parents=True, exist_ok=True)


def jsonl_append(rel: str, line: str, keep: int | None = None,
                 company_dir: str | None = None) -> int:
    """Append one JSON line to a JSONL log under the file lock, keeping at
    most `keep` lines (oldest dropped) when keep is given.

    Single-lock rewrite (not a raw `>>` append) so the append + trim is one
    atomic step and no partial line can be left behind.
    """
    line = line.strip()
    if line:
        json.loads(line)  # reject non-JSON lines loudly
    root = company_root(company_dir)
    p = root / rel
    with locked(root, rel):
        lines: list[str] = []
        if p.is_file():
            for ln in p.read_text().splitlines():
                if ln.strip():
                    lines.append(ln)
        if line:
            lines.append(line)
        if keep is not None and len(lines) > keep:
            lines = lines[-keep:]
        if lines:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name(p.name + ".hive.tmp")
            tmp.write_text("\n".join(lines) + "\n")
            os.replace(tmp, p)
        return len(lines)
