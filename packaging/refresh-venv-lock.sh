#!/usr/bin/env bash
set -euo pipefail

# refresh-venv-lock.sh — regenerate packaging/requirements.lock.txt.
#
# The lock (T1, 0.14) is the released Python environment: exact pins
# for every name in packaging/requirements.txt, marker-aware. When
# requirements.txt changes (a floor bump, a new dependency), re-resolve:
# a fresh throwaway venv installs the FLOORS (what PyPI serves today),
# and this script writes the resolved versions back as the lock — the
# marker of each requirement line is carried over verbatim (under the
# 0.15 floor no line carries a marker — tomli retired with 3.11).
#
# A name whose marker does not apply to the resolution interpreter is
# resolved explicitly against its floor, so the lock still carries an
# exact pin for that marker's audience.
#
# Usage: refresh-venv-lock.sh [--python PY]
#   --python  Interpreter for the resolution venv (default: python3)
#
# Commit the regenerated packaging/requirements.lock.txt together with
# the requirements.txt change — build-package.sh self-verifies the lock
# and fails a build whose lock no longer covers requirements.txt.

SOURCE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REQ="$SOURCE_DIR/packaging/requirements.txt"
LOCK="$SOURCE_DIR/packaging/requirements.lock.txt"
PY=python3

while [ $# -gt 0 ]; do
  case "$1" in
    --python)  PY="$2"; shift 2 ;;
    -h|--help)
      sed -n '3,16p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

[ -f "$REQ" ] || { echo "FAIL: $REQ not found" >&2; exit 1; }

WORK="$(mktemp -d -t hive-lock-refresh-XXXXXX)"
trap 'rm -rf "$WORK"' EXIT
VENV="$WORK/venv"

echo "Resolving $REQ into a fresh venv ($PY) ..."
"$PY" -m venv "$VENV"
"$VENV/bin/python" -m ensurepip --quiet 2>/dev/null || true
"$VENV/bin/pip" install --quiet --upgrade pip 2>/dev/null || true
if ! "$VENV/bin/pip" install --quiet -r "$REQ"; then
  echo "FAIL: the fresh venv could not install $REQ — the floors no" >&2
  echo "      longer resolve (PyPI may have dropped the needed versions)." >&2
  exit 1
fi

# Write the lock: one exact pin per name in requirements.txt, in the
# same order, with the requirement's marker carried over verbatim.
if ! "$VENV/bin/python" - "$REQ" "$LOCK" <<'PYEOF'
import re
import subprocess
import sys
import importlib.metadata as meta


def parse_req(raw: str):
    """(name, extras, floor, raw_req, marker) from a requirement line."""
    line = raw.strip()
    req, _, marker = line.partition(";")
    req = req.strip()
    m = re.match(
        r"^([A-Za-z0-9._-]+)(\[[^\]]*\])?\s*(?:>=\s*([0-9][0-9.]*))?(.*)$",
        req)
    if not m:
        raise SystemExit(f"FAIL: cannot parse requirement line: {raw!r}")
    name, extras, floor, _rest = m.group(1), m.group(2) or "", \
        m.group(3), m.group(4)
    if not floor:
        raise SystemExit(f"FAIL: {name!r} has no floor pin (>=X) — the "
                         "lock needs a floor to verify against")
    return name, extras, floor, req.strip(), marker.strip()


lock_lines = []
for raw in open(sys.argv[1]):
    line = raw.split("#", 1)[0].strip()
    if not line:
        continue
    name, extras, floor, req, marker = parse_req(line)
    try:
        version = meta.version(name)
    except meta.PackageNotFoundError:
        # The marker does not apply to THIS interpreter: resolve the
        # name explicitly against its floor so the lock still pins it
        # for the marker's audience.
        print(f"  {name}: marker {marker!r} does not apply to this "
              f"interpreter — resolving '{req}' explicitly")
        r = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet",
             f"{name}{extras}>={floor}"],
            capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit(f"FAIL: {name} could not be resolved "
                             f"against its floor:\n{r.stderr}")
        version = meta.version(name)
    lock_lines.append(f"{name}{extras}=={version}" +
                      (f"; {marker}" if marker else ""))

header = """# Hive venv lock (T1, 0.14) — the Python environment the release installs.
#
# packaging/requirements.txt carries FLOOR pins (>=X): an install today
# and an install next month can resolve different versions. This lock is
# the RELEASED environment: an exact pin for every name in
# requirements.txt, marker-aware. (T4, 0.15: the tomli line retired
# with the 3.11 floor — tomllib is stdlib, so no line carries a
# marker anymore; the machinery stays for the format.)
#
# Consumers (the lock is one list, like the entry set and the
# required-set constant):
#   - install.sh installs FROM this file when it ships with the release
#     (pre-lock releases fall back to requirements.txt with a note).
#   - build-package.sh self-verifies it before staging: every name in
#     requirements.txt has a pin in the lock that satisfies the floor,
#     and a venv built from the lock imports the runtime set — a miss
#     fails the build, naming the entry.
#   - hive doctor cross-checks the installed venv against it (the
#     "venv lock" check); a drifted package is named.
#
# Regenerated when requirements.txt changes:
#   bash packaging/refresh-venv-lock.sh
# Commit the regenerated file with the requirements.txt change.

"""
with open(sys.argv[2], "w") as f:
    f.write(header)
    for line in lock_lines:
        f.write(line + "\n")
PYEOF
then
  echo "FAIL: lock regeneration failed" >&2
  exit 1
fi

echo "Wrote $LOCK:"
sed -n '/^[A-Za-z]/p' "$LOCK" | sed 's/^/  /'
echo ""
echo "Next: commit the lock with the requirements.txt change — build-"
echo "package.sh self-verifies it on the next build and fails the build"
echo "naming any entry the lock no longer covers."
