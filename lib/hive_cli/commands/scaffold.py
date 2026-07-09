"""hive scaffold — manage the per-project .hive/ tweakable scaffold.

Subcommands:
  extract   First-time extract from the global install into <project>/.hive/.
            Errors if .hive/ already exists unless --force.
  update    Re-sync from the current global install. Files the user hasn't
            tweaked get replaced; tweaked files get a .new sibling so the
            user can diff and merge manually.
  diff      Report which scaffold files differ from the manifest (i.e.,
            the user's local tweaks).
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path


def _install_dir() -> Path:
    return Path(os.environ.get("HIVE_INSTALL_DIR",
                               Path(__file__).resolve().parents[3]))


def _project_root(args) -> Path:
    return Path(getattr(args, "project_dir", ".") or ".").resolve()


def handle_extract(args) -> int:
    install = _install_dir()
    script = install / "lib" / "tools" / "scaffold-extract.sh"
    if not script.is_file():
        print(f"ERROR: scaffold-extract.sh missing at {script}", file=sys.stderr)
        return 1
    cmd = ["bash", str(script), str(_project_root(args)),
           "--install-dir", str(install)]
    if getattr(args, "force", False):
        cmd.append("--force")
    return subprocess.run(cmd).returncode


def handle_update(args) -> int:
    install = _install_dir()
    script = install / "lib" / "tools" / "scaffold-update.sh"
    if not script.is_file():
        print(f"ERROR: scaffold-update.sh missing at {script}", file=sys.stderr)
        return 1
    cmd = ["bash", str(script), str(_project_root(args)),
           "--install-dir", str(install)]
    if getattr(args, "dry_run", False):
        cmd.append("--dry-run")
    return subprocess.run(cmd).returncode


def handle_diff(args) -> int:
    project = _project_root(args)
    scaffold = project / ".hive"
    manifest_path = scaffold / ".manifest"
    if not scaffold.is_dir():
        print(f"ERROR: {scaffold} does not exist; run 'hive scaffold extract' first",
              file=sys.stderr)
        return 1
    if not manifest_path.is_file():
        print(f"ERROR: {manifest_path} missing; scaffold corrupt", file=sys.stderr)
        return 1
    try:
        manifest = json.loads(manifest_path.read_text())
    except json.JSONDecodeError as exc:
        print(f"ERROR: manifest parse failed: {exc}", file=sys.stderr)
        return 1

    tweaked = []
    deleted = []
    for rel, sha_orig in sorted(manifest.items()):
        f = scaffold / rel
        if not f.is_file():
            deleted.append(rel)
            continue
        h = hashlib.sha256(f.read_bytes()).hexdigest()
        if h != sha_orig:
            tweaked.append(rel)

    # Also flag files in the scaffold not in the manifest (locally-added).
    added = []
    for f in scaffold.rglob("*"):
        if not f.is_file():
            continue
        if f.name == ".manifest" or f.suffix == ".new":
            continue
        rel = str(f.relative_to(scaffold))
        if rel not in manifest:
            added.append(rel)

    if not (tweaked or deleted or added):
        print("scaffold is pristine (no local tweaks)")
        return 0

    if tweaked:
        print(f"Tweaked ({len(tweaked)}):")
        for p in tweaked:
            print(f"  M  {p}")
    if added:
        print(f"Added locally ({len(added)}):")
        for p in added:
            print(f"  ?  {p}")
    if deleted:
        print(f"Deleted locally ({len(deleted)}):")
        for p in deleted:
            print(f"  D  {p}")
    return 0


def handle(args) -> int:
    action = getattr(args, "scaffold_action", None)
    if action == "extract":
        return handle_extract(args)
    if action == "update":
        return handle_update(args)
    if action == "diff":
        return handle_diff(args)
    print("usage: hive scaffold {extract|update|diff} [project_dir]", file=sys.stderr)
    return 2
