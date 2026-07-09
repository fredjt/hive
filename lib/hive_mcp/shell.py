"""Subprocess helper for MCP tools that wrap bash scripts.

Each MCP tool shells out to a script in <project>/.hive/lib/tools/ (per-project
scaffold). This module centralizes script-path resolution, env handling,
output parsing, and error mapping so tool modules stay tiny.

Scaffold resolution order, per call:
  1. company_dir argument → project_root → project_root/.hive/lib/tools
  2. $HIVE_ROOT env var (set by bin/hive when project sentinel was found)
  3. $HIVE_INSTALL_DIR/lib/tools (dev/legacy fallback)
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# Where the global install (venv, MCP source, dashboard source) lives. Set
# by the bin/hive launcher; falls back to walking up from this file for
# direct-from-source runs.
HIVE_INSTALL_DIR = Path(os.environ.get("HIVE_INSTALL_DIR",
                                       Path(__file__).resolve().parents[2]))

# Default scaffold root if no per-call company_dir was supplied. Honors the
# launcher's HIVE_ROOT, then falls back to the install dir (so direct-from-
# source dev runs still work).
HIVE_ROOT = Path(os.environ.get("HIVE_ROOT", HIVE_INSTALL_DIR))


def _resolve_scaffold(company_dir: str | None) -> Path:
    """Resolve the .hive/ scaffold root for this call.

    company_dir wins because it lets a long-running MCP server serve multiple
    projects: each tool call carries the project context. When omitted, fall
    back to the env-set HIVE_ROOT, then to the install dir.
    """
    if company_dir:
        project_root = Path(company_dir).resolve().parent
        candidate = project_root / ".hive"
        if candidate.is_dir():
            return candidate
    return HIVE_ROOT


@dataclass
class ShellResult:
    """Structured result of a shell-script invocation."""

    ok: bool
    exit_code: int
    stdout: str
    stderr: str
    parsed: Any = None  # JSON-decoded stdout if parseable, else None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "ok": self.ok,
            "exit_code": self.exit_code,
            "stderr": self.stderr.strip(),
        }
        if self.parsed is not None:
            d["data"] = self.parsed
        else:
            d["stdout"] = self.stdout.strip()
        return d


def run(
    script: str,
    *args: str,
    env: dict[str, str] | None = None,
    company_dir: str | None = None,
    stdin: str | None = None,
    timeout: int = 60,
    parse_json: bool = True,
) -> ShellResult:
    """Run lib/tools/<script>(.sh) with positional args.

    Args:
        script: Script base name. ".sh" appended if not present.
        *args: Positional arguments. None values are filtered out.
        env: Extra environment variables to set.
        company_dir: Sets COMPANY_DIR for the subprocess. Auto-detected from
            CWD's .company/ directory if not specified.
        stdin: Optional stdin content (used by --stdin scripts).
        timeout: Subprocess timeout in seconds.
        parse_json: Try to JSON-decode stdout. Falls back silently to raw text.

    Returns:
        ShellResult with ok/exit_code/stdout/stderr/parsed.
    """
    if not script.endswith(".sh"):
        script = script + ".sh"

    # Resolve COMPANY_DIR first so scaffold resolution can use it.
    resolved_company_dir = company_dir
    if not resolved_company_dir:
        cand = Path.cwd() / ".company"
        if cand.is_dir():
            resolved_company_dir = str(cand)

    scaffold = _resolve_scaffold(resolved_company_dir)
    script_path = scaffold / "lib" / "tools" / script
    if not script_path.exists():
        return ShellResult(
            ok=False,
            exit_code=127,
            stdout="",
            stderr=f"script not found: {script_path}",
        )

    full_env = os.environ.copy()
    if resolved_company_dir:
        full_env["COMPANY_DIR"] = str(resolved_company_dir)
    full_env["SKILL_DIR"] = str(scaffold)
    full_env["HIVE_ROOT"] = str(scaffold)
    full_env["HIVE_INSTALL_DIR"] = str(HIVE_INSTALL_DIR)
    if env:
        full_env.update(env)

    cmd = ["bash", str(script_path), *(a for a in args if a is not None)]

    try:
        proc = subprocess.run(
            cmd,
            input=stdin,
            capture_output=True,
            text=True,
            env=full_env,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return ShellResult(
            ok=False,
            exit_code=124,
            stdout=exc.stdout or "",
            stderr=f"timeout after {timeout}s: {exc.stderr or ''}",
        )

    parsed = None
    if parse_json and proc.stdout.strip():
        try:
            parsed = json.loads(proc.stdout)
        except json.JSONDecodeError:
            parsed = None

    return ShellResult(
        ok=(proc.returncode == 0),
        exit_code=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        parsed=parsed,
    )


def project_root_from_company_dir(company_dir: str) -> str:
    """Given a COMPANY_DIR path, return its parent (the project root)."""
    return str(Path(company_dir).resolve().parent)
