"""Install-path resolution — the ONE place hive paths are derived (G9).

The install root (HIVE_ROOT: the directory holding lib/, VERSION, venv/,
etc/) is resolved once, in one order:

  1. ``HIVE_ROOT`` env var  — set by bin/hive for daemons and spawns
  2. ``HIVE_INSTALL_DIR``   — set by the test suite; older tooling
  3. derived from this file — lib/hive_paths.py -> lib/.. (dev checkout)

An env value that does not point at a real install root (no lib/
directory) is rejected and the chain falls through — the failure mode
the old per-module __file__-climbing chains produced (wrong subdirectory,
extra /hive hop) is now unrepresentable: there is exactly one chain.

Bash scripts resolve their own copy from $HIVE_ROOT/$SKILL_DIR; they
cannot import Python for trivial paths and are out of scope here.
"""

from __future__ import annotations

import os
from pathlib import Path


def _derived_root() -> Path:
    # This file lives at <root>/lib/hive_paths.py
    return Path(__file__).resolve().parent.parent


def install_root() -> Path:
    """The install root, resolved per the module docstring's order."""
    for var in ("HIVE_ROOT", "HIVE_INSTALL_DIR"):
        value = os.environ.get(var)
        if value:
            candidate = Path(value)
            if (candidate / "lib").is_dir():
                return candidate
    return _derived_root()


def lib_dir() -> Path:
    return install_root() / "lib"


def core_dir() -> Path:
    return lib_dir() / "core"


def agents_dir() -> Path:
    return lib_dir() / "agents"


def tools_dir() -> Path:
    return lib_dir() / "tools"


def version_file() -> Path:
    return install_root() / "VERSION"


def company_dir(project_root: str | Path | None = None) -> Path:
    """The live .company/ state directory for a project.

    COMPANY_DIR env wins (spawns set it); otherwise
    <project_root>/.company (default: the current directory).
    """
    value = os.environ.get("COMPANY_DIR")
    if value:
        return Path(value)
    return Path(project_root or ".") / ".company"