"""Shared helpers for CLI subcommand handlers."""

from __future__ import annotations

import os
from pathlib import Path


def company_dir() -> str | None:
    """The .company directory a command should target.

    Resolution order is the codebase-wide convention — every bash tool
    resolves ``COMPANY_DIR="${COMPANY_DIR:-$PWD/.company}"`` and the MCP
    tools fall back to the env var when the caller passes no
    ``company_dir``: an explicit, non-empty COMPANY_DIR environment
    variable wins (even over a CWD .company, so a checkout carrying a
    live company never hijacks commands aimed elsewhere); otherwise the
    company in the current directory; otherwise None (the MCP server
    then falls back to its own CWD). `hive llm stats` already resolves
    this way.
    """
    env = os.environ.get("COMPANY_DIR")
    if env:
        return env
    cwd_company = Path.cwd() / ".company"
    return str(cwd_company) if cwd_company.is_dir() else None
