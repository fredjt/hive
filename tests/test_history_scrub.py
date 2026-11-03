"""A.2c: permanent history-scrub guard.

The 0.7.0 declassification release scrubbed six private paths out of the
FULL git history (they held a live GitHub OAuth token, the owner identity,
and absolute home paths). This test pins that scrub: it fails the suite the
moment any scrubbed path re-enters any commit, or any credential/identity
pattern re-appears in any object in the repository.

Complements the gitleaks CI job (C.1): gitleaks applies generic signature
patterns to new work; this test pins the exact private paths and owner
identity strings for THIS repo across the entire object database.
"""
import io
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# A.2: paths removed from all history with `git filter-repo --invert-paths`.
# Each one held private state: gh CLI auth (OAuth token), owner identity +
# credential-helper wiring, gh device-id, REST/HTTP caches (Request-Ids,
# OAuth scopes, absolute home path), a buildx lock, and project-local
# scaffold (design notes + cycle images).
SCRUBBED_PATHS = [".config", ".gitconfig", ".local", ".cache", ".docker", ".hive"]

# Credential signature patterns (the A.2b full-blob sweep set).
# NOTE: pattern literals whose full text would match themselves (the
# private-key header in particular) are assembled from fragments so this
# guard's own source blob stays clean under the sweep it pins — the same
# trick test_zero_trace.py uses for the legacy token.
CREDENTIAL_PATTERNS = [
    re.compile(rb"gho_[A-Za-z0-9]{8,}"),          # GitHub OAuth token
    re.compile(rb"ghp_[A-Za-z0-9]{8,}"),          # GitHub PAT
    re.compile(rb"github_pat_[A-Za-z0-9_]{8,}"),  # GitHub fine-grained PAT
    re.compile(rb"sk-[A-Za-z0-9]{16,}"),          # LLM provider API key
    re.compile(rb"AKIA[0-9A-Z]{16}"),             # AWS access key id
    re.compile(b"PRIVATE " + b"KEY"),              # private key block header
    re.compile(rb"xox[baprs]-[A-Za-z0-9-]{8,}"),  # Slack token
    re.compile(rb"eyJ[A-Za-z0-9_-]{10,}\\.[A-Za-z0-9_-]{5,}"),  # JWT
]

# Owner identity strings, assembled from fragments for the same reason
# (no fragment contains a full pattern, so the sweep never matches this
# file itself).
_HOME = b"/home/" + b"fredt"
_USER_ID = b"61" + b"0651" + b"87"
_NOREPLY = _USER_ID + b"+fredjt@users" + b".noreply" + b".github" + b".com"

IDENTITY_PATTERNS = [
    re.compile(_HOME),           # home path
    re.compile(re.escape(_NOREPLY)),  # noreply email
    re.compile(_USER_ID),        # numeric user id
]


def _git(*args, check=True):
    return subprocess.run(
        ["git", "-C", str(REPO), *args],
        capture_output=True, check=check, timeout=120,
    )


def test_scrubbed_paths_absent_from_all_history():
    """`git log --all -- <path>` is empty for every A.2-scrubbed path."""
    for path in SCRUBBED_PATHS:
        out = _git("log", "--all", "--oneline", "--", path)
        assert out.stdout == b"", (
            f"A.2 scrub violated: '{path}' exists in history again:\n"
            f"{out.stdout.decode(errors='replace')[:500]}"
        )


def _iter_blobs():
    """Yield (sha, data) for every blob object in the repository.

    One `git cat-file --batch-all-objects --batch` subprocess keeps this
    O(objects) instead of O(objects * fork)."""
    proc = _git("cat-file", "--batch-all-objects", "--batch")
    buf = io.BytesIO(proc.stdout)
    while True:
        line = buf.readline()
        if not line:
            break
        parts = line.decode("ascii", "replace").split()
        if len(parts) < 3:
            continue
        sha, typ, size = parts[0], parts[1], int(parts[2])
        data = buf.read(size)
        buf.read(1)  # trailing newline after the object payload
        if typ == "blob":
            yield sha, data


def test_no_private_content_in_any_object():
    """A.2b gate: zero credential/identity pattern hits in ANY object.

    Scans every blob reachable from any ref. The only intentional
    identity-looking string in the tree is the `github.com/fredjt/hive`
    openrouter attribution header, which matches none of the patterns
    above, so no exclusions are needed. A future mis-commit of a private
    path or an owner identity string fails the suite the moment it lands.
    """
    hits = []
    scanned = 0
    for sha, data in _iter_blobs():
        scanned += 1
        for pattern in CREDENTIAL_PATTERNS + IDENTITY_PATTERNS:
            for match in pattern.finditer(data):
                context = data[max(0, match.start() - 40):match.end() + 40]
                hits.append(
                    f"  {pattern.pattern!r} in {sha[:12]}: {context!r}"
                )
    assert scanned > 0, "object database unexpectedly empty — not a git repo?"
    assert not hits, (
        f"A.2 scrub violated: {len(hits)} private pattern hit(s) in "
        f"{scanned} blobs:\n" + "\n".join(hits[:20])
    )
