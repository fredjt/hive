"""venv_prune — the installed venv is EXACTLY the released environment.

T1 (0.15): one list, like the entry set. A 0.14 install resolved the
FLOORS (or the lock, T1 0.14); an operator or a prior upgrade can also
leave packages in the venv that the release does not define (a hand
pip install, a dependency a newer release dropped, a leftover from a
hand-edited requirements). The exact venv set is:

  - the LOCK CLOSURE: the direct names in
    packaging/requirements.lock.txt plus their transitive dependencies
    (resolved through the venv's own importlib.metadata), and
  - the VENV TOOLING: pip, setuptools, wheel (plus uv when the venv
    carries it) — the named exceptions: the venv's own management
    surface, never release residue.

Shared by install.sh (the upgrade: the reused venv is pruned after the
pip install, every removal named) and by hive doctor (the installed-set
check FAILs on an installed name outside the set, naming it and the fix
— re-run the install). Stdlib only: it runs under the venv's OWN
python (its metadata is the ground truth), launched with the install
tree on PYTHONPATH — no dependency on what the venv contains.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import unicodedata
from importlib import metadata
from pathlib import Path

# The named exceptions: the venv's own management surface (tooling the
# venv needs to manage itself, never release residue).
VENV_TOOLING = ("pip", "setuptools", "wheel", "uv")


def normalize(name: str) -> str:
    """PEP 503 name normalization (extras stripped; case, runs of
    -/_/.)."""
    name = name.split("[")[0]
    return re.sub(r"[-_.]+", "-", name).lower()


def lock_direct_names(lock_path: Path) -> set:
    """The lock's DIRECT names (exact pins, marker-aware parse)."""
    names = set()
    for line in lock_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        req = line.split(";")[0].strip()
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\[?[^<>=!~;]*\]?", req)
        if m:
            names.add(normalize(m.group(1)))
    return names


def site_packages(venv: Path):
    """The venv's site-packages dirs (POSIX: lib/pythonX.Y/site-packages)."""
    out = []
    for pydir in sorted((venv / "lib").glob("python*.*")):
        sp = pydir / "site-packages"
        if sp.is_dir():
            out.append(sp)
    return out


def closure(names: set, venv: Path = None) -> set:
    """names + their transitive requires. Resolved against the target
    venv's metadata when venv is given — otherwise the caller's own
    environment (install.sh runs the module under the venv's OWN
    python, so both spellings are the venv)."""
    seen = set(names)
    stack = list(names)
    spaths = [str(s) for s in site_packages(venv)] if venv is not None else None
    while stack:
        n = stack.pop()
        dist = None
        if spaths is not None:
            for d in metadata.distributions(path=spaths):
                if normalize(str(getattr(d, "name", "") or "")) == n:
                    dist = d
                    break
        else:
            try:
                dist = metadata.distribution(n)
            except metadata.PackageNotFoundError:
                dist = None
        if dist is None:
            continue
        for req in (dist.requires or []):
            m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)", req)
            if not m:
                continue
            d = normalize(m.group(1))
            if d not in seen:
                seen.add(d)
                stack.append(d)
    return seen


def installed_names(venv: Path) -> set:
    """The venv's installed distributions — its site-packages' metadata
    (NOT the caller's environment: the module is stdlib-only so it can
    run under ANY python, but the ground truth is always the target
    venv)."""
    names = set()
    for sp in site_packages(venv):
        for entry in metadata.distributions(path=[str(sp)]):
            name = getattr(entry, "name", None)
            if name:
                names.add(normalize(str(name)))
    return names


def exact_venv_set(lock_path: Path, venv: Path = None) -> set:
    return closure(lock_direct_names(lock_path), venv=venv) | {
        normalize(t) for t in VENV_TOOLING}


def unexpected(venv: Path, lock_path: Path) -> list:
    """Installed names outside the exact set (sorted, normalized).
    The closure is resolved against the venv's OWN metadata."""
    exact = exact_venv_set(lock_path, venv=venv)
    return sorted(n for n in installed_names(venv) if n not in exact)


def prune(venv: Path, lock_path: Path, dry_run: bool = False) -> list:
    """Remove the unexpected names with the venv's OWN pip. Returns
    the names removed (or, dry-run, that WOULD be removed). Every
    removal is printed, naming the package."""
    victims = unexpected(venv, lock_path)
    removed = []
    for name in victims:
        if dry_run:
            print(f"  would remove: {name} (outside the released venv set)")
            removed.append(name)
            continue
        py = venv / "bin" / "python"
        r = subprocess.run(
            [str(py), "-m", "pip", "uninstall", "-y", name],
            capture_output=True, text=True, timeout=300)
        if name not in installed_names(venv):
            print(f"  removed: {name} (outside the released venv set)")
            removed.append(name)
            continue
        # pip left it (a package with no RECORD — a planted/hand-made
        # dist-info — is "not installed" to pip 23.1+): remove its
        # dist-info/egg-info directories from the venv's site-packages
        # directly (the metadata IS the install, for our purposes).
        for sp in site_packages(venv):
            for d in list(sp.glob("*.dist-info")) + list(sp.glob("*.egg-info")):
                if _dist_name(d) is not None and \
                        normalize(_dist_name(d)) == name:
                    _rmtree(d)
        if name not in installed_names(venv):
            print(f"  removed (dist-info fallback): {name} "
                  f"(outside the released venv set)")
            removed.append(name)
        else:
            tail = (r.stderr or r.stdout).strip().splitlines()
            print(f"  WARNING: could not remove {name}: "
                  f"{tail[-1] if tail else 'pip failed'}",
                  file=sys.stderr)
    return removed


def _dist_name(d: Path):
    """The distribution name of a dist-info/egg-info dir — from its
    METADATA/PKG-INFO (the dir name carries the version, which is
    ambiguous to split)."""
    for meta in (d / "METADATA", d / "PKG-INFO"):
        if meta.exists():
            try:
                for line in meta.read_text(errors="replace").splitlines():
                    if line.lower().startswith("name:"):
                        return line.split(":", 1)[1].strip()
            except OSError:
                return None
    return None


def _rmtree(path: Path) -> None:
    import shutil
    shutil.rmtree(path, ignore_errors=True)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Prune an installed venv to the exact released "
                    "environment (the lock closure + venv tooling).")
    p.add_argument("--venv", required=True,
                   help="the venv directory (its bin/python is the "
                        "interpreter whose metadata is the ground truth)")
    p.add_argument("--lock", required=True,
                   help="packaging/requirements.lock.txt (the released "
                        "environment)")
    p.add_argument("--dry-run", action="store_true",
                   help="name what would be removed; remove nothing")
    p.add_argument("--list", action="store_true",
                   help="print the installed names outside the exact "
                        "set, one per line; change nothing (the "
                        "verify-release.sh --from mode reads this)")
    a = p.parse_args(argv)
    venv = Path(a.venv)
    lock = Path(a.lock)
    if not (venv / "bin" / "python").exists():
        print(f"venv_prune: no venv python at {venv}", file=sys.stderr)
        return 1
    if not lock.is_file():
        print(f"venv_prune: no lock at {lock}", file=sys.stderr)
        return 1
    if a.list:
        for n in unexpected(venv, lock):
            print(n)
        return 0
    victims = prune(venv, lock, dry_run=a.dry_run)
    if not victims:
        print("  venv is exact — every installed name is in the "
              "released set (lock closure + tooling)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
