"""hive doctor — check that everything is wired up."""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import subprocess
from pathlib import Path

from hive_cli import mcp_client
from hive_cli.commands.company import _pid_alive


# HIVE_INSTALL_DIR — global install (venv, MCP/CLI/dashboard source).
HIVE_INSTALL_DIR = Path(os.environ.get("HIVE_INSTALL_DIR",
                                       Path(__file__).resolve().parents[3]))
# HIVE_ROOT — global install root (projects use installed scripts directly).
HIVE_ROOT = Path(os.environ.get("HIVE_ROOT", HIVE_INSTALL_DIR))


# C1: run context — handle() sets JSON mode + the check/warning/llm sinks
# here so the pretty body below can stay untouched while --json collects
# the same checks into a machine-readable dump.
_RUN = {"json": False, "checks": None, "warnings": None, "llm": None}


def _check(name: str, ok: bool, detail: str = "") -> bool:
    checks = _RUN.get("checks")
    if checks is not None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})
    if _RUN.get("json"):
        return bool(ok)
    sym = "✓" if ok else "✗"
    color = "\033[32m" if ok else "\033[31m"
    print(f" {color}{sym}\033[0m {name:<35} {detail}")
    return bool(ok)


def _probe_endpoint(base_url: str, timeout: float = 5.0) -> tuple[bool, str]:
    """B.4 --deep: TCP-connect probe of the LLM endpoint. Deliberately
    transport-level (no LLM request, no token spend): it answers 'can a
    daemon reach the endpoint at all', which is the failure class that
    otherwise surfaces mid-sprint as unreachable-agent errors."""
    import socket
    from urllib.parse import urlparse
    u = urlparse(base_url)
    if not u.hostname:
        return False, f"no host in base_url {base_url!r}"
    port = u.port or (443 if u.scheme == "https" else 80)
    try:
        with socket.create_connection((u.hostname, port), timeout=timeout):
            return True, f"TCP {u.hostname}:{port} reachable"
    except Exception as e:
        return False, f"TCP {u.hostname}:{port} failed: {e}"


def _publisher_from_config(text: str) -> str:
    """Extract publisher_script the same way release-publish-runner.sh does
    (first `publisher_script = "..."` line; value trimmed, surrounding
    double quotes stripped). Returns "" when unset/empty."""
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("publisher_script") or s.startswith("#"):
            continue
        if "=" not in s:
            continue
        val = s.split("=", 1)[1].strip()
        if len(val) >= 2 and val.startswith('"') and val.endswith('"'):
            val = val[1:-1]
        return val
    return ""


def resolve_publisher(config_path, default_publisher):
    """Resolve the release publisher script, mirroring
    release-publish-runner.sh: a non-empty [release].publisher_script in the
    config TOML wins; otherwise the built-in default (github.sh).

    Returns (publisher_path, source) where source is "config override" or
    "built-in default"."""
    config_path = Path(config_path)
    if config_path.is_file():
        try:
            val = _publisher_from_config(config_path.read_text())
        except OSError:
            val = ""
        if val:
            return Path(val), "config override"
    return Path(default_publisher), "built-in default"


def _toml_loads_file(path: Path) -> dict:
    try:
        import tomllib
    except ImportError:  # Python 3.10 (CI matrix) — CI installs tomli
        import tomli as tomllib
    return tomllib.loads(path.read_text()) or {}


def _config_integrity_check() -> bool:
    """A1: a typo'd section or key in the user config must be discoverable
    in seconds, not in a failed sprint. The user file is validated against
    the defaults schema (etc/config.defaults.toml is itself the schema —
    anything unknown in the merged config comes from the user file). Each
    offender is reported with a difflib close-match suggestion so the fix
    is visible on the line: 'unknown key llms.provder — did you mean
    llm.provider?'."""
    print("\nConfig integrity:")
    core_dir = str(HIVE_ROOT / "lib" / "core")
    if core_dir not in sys.path:
        sys.path.insert(0, core_dir)
    from llm_providers import (known_paths, load_defaults, suggest_close,
                               unknown_config_keys)
    schema = load_defaults(HIVE_ROOT)
    if not schema:
        print(f" \033[33m·\033[0m {'config keys':<35} "
              f"no defaults schema at {HIVE_ROOT / 'etc' / 'config.defaults.toml'}")
        return True
    cfg_path = Path(os.environ.get("HIVE_CONFIG",
                                   Path.home() / ".config" / "hive" / "config.toml"))
    user_cfg = {}
    if cfg_path.is_file():
        try:
            user_cfg = _toml_loads_file(cfg_path) or {}
        except Exception as e:
            return _check("config keys (user file)", False,
                          f"{cfg_path} does not parse as TOML: {e}")
    unknowns = unknown_config_keys(user_cfg, schema)
    if not unknowns:
        return _check("config keys (user file)", True,
                      f"all known ({cfg_path})" if cfg_path.is_file()
                      else "no user config — defaults only")
    candidates = known_paths(schema) + list(schema.keys())
    lines = []
    for u in unknowns:
        kind = "section" if "." not in u else "key"
        msg = f"unknown {kind} {u}"
        sugg = suggest_close(u, candidates)
        if sugg:
            msg += " — did you mean " + " or ".join(sugg) + "?"
        lines.append(msg)
    return _check("config keys (user file)", False, "; ".join(lines))


def _pyproject_version(pyproject_path) -> str | None:
    """Read the top-level [project] version field from a pyproject.toml
    (naive line scan — the format is ours). None if absent/unreadable."""
    try:
        text = Path(pyproject_path).read_text()
    except OSError:
        return None
    m = re.search(r'(?m)^\s*version\s*=\s*"([^"]+)"', text)
    return m.group(1) if m else None


def _release_checks() -> bool:
    """C.6: doctor checks for the release path. Doctor previously stopped at
    venv/binaries, but the publish stage can still fail on a missing gh CLI
    or a non-executable publisher (release-publish-runner.sh exit 3), and a
    VERSION-file-vs-tree drift mislabels releases. Returns True iff all
    release-path checks pass."""
    ok = True
    print("\nRelease path:")

    # VERSION file — source of truth for hive --version and the release
    # build. Missing → builds fall back to 0.0.0-dev and --version lies.
    version_file = HIVE_INSTALL_DIR / "VERSION"
    version = ""
    if version_file.is_file():
        version = version_file.read_text().strip()
    ok &= _check("VERSION file", bool(version),
                 version if version else f"missing: {version_file}")

    # Resolved publisher script (config override or built-in default) must
    # exist AND be executable — the exact requirement the publish stage
    # enforces at release time (exit 3 otherwise).
    cfg_path = Path(os.environ.get("HIVE_CONFIG",
                                   Path.home() / ".config" / "hive" / "config.toml"))
    default_pub = HIVE_ROOT / "lib" / "templates" / "publishers" / "github.sh"
    publisher, pub_source = resolve_publisher(cfg_path, default_pub)
    pub_ok = publisher.is_file() and os.access(publisher, os.X_OK)
    detail = str(publisher)
    if pub_source == "config override":
        detail += " (config override)"
    if not pub_ok:
        detail += " — missing or not executable"
    ok &= _check("publisher script", pub_ok, detail)

    # gh CLI — required by the built-in github publisher; a custom
    # publisher is responsible for its own dependencies.
    if publisher.resolve() == default_pub.resolve():
        ok &= _check("gh CLI (default publisher)", shutil.which("gh") is not None)
    else:
        print(f" \033[33m·\033[0m {'gh CLI':<35} not required (custom publisher in effect)")

    # VERSION-file-vs-installed-tree consistency: packaging/pyproject.toml
    # ships in the install tree and carries its own version field — when it
    # drifts from VERSION, pip-installed metadata mislabels the release.
    pyproject = HIVE_INSTALL_DIR / "packaging" / "pyproject.toml"
    if version and pyproject.is_file():
        tree_version = _pyproject_version(pyproject)
        if tree_version is None:
            print(f" \033[33m⚠\033[0m {'VERSION vs pyproject':<35} "
                  f"no version field in {pyproject}")
        else:
            match = version == tree_version
            ok &= _check("VERSION matches pyproject", match,
                         f"VERSION={version} pyproject={tree_version}"
                         + ("" if match else " — STALE"))
    return ok


def handle(args) -> int:
    """Run every check; exit 0 iff all pass. C1: with --json the pretty
    output is replaced by a machine-readable dump (every check as
    name/ok/detail, plus the resolved LLM identity) on stdout — the exit
    code is the same either way."""
    json_mode = bool(getattr(args, "json", False))
    checks: list = []
    warnings: list = []
    llm_info: dict = {}
    _RUN.update({"json": json_mode, "checks": checks,
                 "warnings": warnings, "llm": llm_info})
    if json_mode:
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            all_ok = _run_checks(args)
    else:
        all_ok = _run_checks(args)
    payload = {"ok": bool(all_ok), "checks": checks,
               "warnings": warnings, "llm": llm_info}
    version_file = HIVE_INSTALL_DIR / "VERSION"
    if version_file.is_file():
        payload["version"] = version_file.read_text().strip()
    _save_doctor_dump(args, payload)
    if json_mode:
        print(json.dumps(payload, indent=2))
    return 0 if all_ok else 1


def _save_doctor_dump(args, payload: dict) -> None:
    """C2: persist the dump to <project>/.company/state/doctor.json so the
    dashboard's Config panel can render the last run without spawning
    anything. Best-effort — a failed write never fails the doctor run."""
    try:
        from datetime import datetime, timezone
        project_dir = Path(getattr(args, "project_dir", ".")).resolve()
        company_dir = project_dir / ".company"
        if not company_dir.is_dir():
            return
        state_dir = company_dir / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        payload = dict(payload)
        payload["ts"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        (state_dir / "doctor.json").write_text(json.dumps(payload, indent=2))
    except Exception:
        pass


def _run_checks(args) -> bool:
    print("Hive doctor — checking installation\n")
    all_ok = True

    # Global install directory.
    all_ok &= _check("HIVE_INSTALL_DIR exists", HIVE_INSTALL_DIR.is_dir(),
                     str(HIVE_INSTALL_DIR))
    all_ok &= _check("HIVE_ROOT", HIVE_ROOT.is_dir(), str(HIVE_ROOT))

    # Install-dir subdirs (where venv + source live).
    for sub in ("bin", "lib/hive_mcp", "lib/hive_cli", "lib/dashboard"):
        p = HIVE_INSTALL_DIR / sub
        all_ok &= _check(f"  install/{sub}/", p.is_dir())
    # Install subdirs (used by tools, agents, core).
    for sub in ("lib/tools", "lib/agents", "lib/core"):
        p = HIVE_ROOT / sub
        all_ok &= _check(f"  scaffold/{sub}/", p.is_dir())

    # Python venv (always from install dir).
    venv_py = HIVE_INSTALL_DIR / "venv" / "bin" / "python"
    all_ok &= _check("Python venv", venv_py.is_file(), str(venv_py))

    if venv_py.is_file():
        # tomllib is stdlib on Python 3.11+; fall back to tomli on older versions
        check_code = (
            "import mcp; import fastapi; import httpx; "
            "import sys; "
            "(__import__('tomllib') if sys.version_info >= (3,11) else __import__('tomli'))"
        )
        try:
            r = subprocess.run([str(venv_py), "-c", check_code],
                               capture_output=True, text=True)
            all_ok &= _check("Required Python packages", r.returncode == 0,
                             r.stderr.strip().splitlines()[-1] if r.returncode != 0 else "mcp, fastapi, httpx, tomllib/tomli")
        except Exception as e:
            all_ok &= _check("Required Python packages", False, str(e))

    # CLI
    all_ok &= _check("hive on PATH", shutil.which("hive") is not None)

    # External tools
    # Agent harness: Python-based, no external binary needed
    harness_path = HIVE_ROOT / "lib" / "core" / "agent-harness.py"
    all_ok &= _check("agent harness", harness_path.is_file(),
                     f"{harness_path} (Python-based, no CLI required)")

    # LLM provider resolution (reports the resolved provider/model and the
    # key's source — never the key value itself).
    print("\nLLM provider:")
    try:
        core_dir = str(HIVE_ROOT / "lib" / "core")
        if core_dir not in sys.path:
            sys.path.insert(0, core_dir)
        from llm_providers import resolve as _resolve_llm
        from llm_providers import unconfigured_hint
        llm_res = _resolve_llm(hive_root=HIVE_ROOT)
        if llm_res.key_from_env:
            key_src = "env " + llm_res.key_from_env
        elif llm_res.key_in_config:
            key_src = "config [llm].api_key"
        else:
            key_src = "none"
        # C1: the resolved identity goes into the JSON dump (key SOURCE
        # label only — never the key value itself).
        if _RUN.get("llm") is not None:
            _RUN["llm"].update({
                "configured": bool(llm_res.is_configured),
                "provider": llm_res.provider,
                "model": llm_res.model,
                "base_url": llm_res.base_url,
                "key_source": key_src,
            })
        if llm_res.is_configured:
            all_ok &= _check("LLM provider configured", True,
                             "provider={0} model={1} base={2} key={3}".format(
                                 llm_res.provider or "?", llm_res.model or "(default)",
                                 llm_res.base_url or "(default)", key_src))
        else:
            # C4: name the exact file (the hint lives in llm_providers so
            # `hive llm test` — B1 — reuses the very same message).
            all_ok &= _check("LLM provider configured", False,
                             unconfigured_hint(hive_root=HIVE_ROOT))
        for w in llm_res.warnings:
            if _RUN.get("warnings") is not None:
                _RUN["warnings"].append(w)
            print("  warning: " + w)
        # B.4: optional reachability probe — behind --deep so a plain
        # `hive doctor` stays offline-safe.
        deep = getattr(args, "deep", False)
        if deep and llm_res.is_configured and llm_res.base_url:
            ok, detail = _probe_endpoint(llm_res.base_url)
            all_ok &= _check("LLM endpoint reachable (--deep)", ok, detail)
    except Exception as e:
        all_ok &= _check("LLM provider resolution", False, str(e))

    # Config integrity (A1): unknown sections/keys in the user config fail
    # here with a close-match suggestion — a typo'd provider must not ship
    # invisibly into a failed sprint.
    all_ok &= _config_integrity_check()

    all_ok &= _check("git", shutil.which("git") is not None)
    all_ok &= _check("jq", shutil.which("jq") is not None)
    all_ok &= _check("node (for dashboard)", shutil.which("node") is not None)
    all_ok &= _check("bash", shutil.which("bash") is not None)

    # MCP server reachable?
    print("\nMCP server:")
    tools = []
    try:
        tools = mcp_client.list_tools()
        all_ok &= _check("MCP server tools", len(tools) > 0, f"{len(tools)} tools registered")
    except Exception as e:
        all_ok &= _check("MCP server", False, str(e))

    # Core scripts present
    print("\nCore scripts:")
    for s in ("agent-spawn.sh", "agent-harness.py", "init.sh", "orchestrator.sh"):
        p = HIVE_ROOT / "lib" / "core" / s
        all_ok &= _check(f"  {s}", p.is_file())

    # Sample tool scripts
    print("\nSample tool scripts:")
    for s in ("ticket-create.sh", "verification-gates.sh", "ticket-approve.sh",
              "roster-rebuild.sh", "status-rebuild.sh", "company-start.sh"):
        p = HIVE_ROOT / "lib" / "tools" / s
        all_ok &= _check(f"  {s}", p.is_file())

    # Release path (VERSION, publisher script, gh CLI, metadata drift).
    all_ok &= _release_checks()

    # Company daemon health — only when we're in a project with a .company
    # workspace. A daemon marked dead by the supervisor (restart budget
    # exhausted) needs manual intervention, so it fails the doctor run.
    project_dir = Path(getattr(args, "project_dir", ".")).resolve()
    company_dir = project_dir / ".company"
    procs_file = company_dir / "processes.json"
    if company_dir.is_dir() or procs_file.is_file():
        print("\nDaemon health (company supervisor):")
        procs = []
        if procs_file.is_file():
            try:
                procs = json.loads(procs_file.read_text()).get("processes", [])
            except json.JSONDecodeError:
                pass
        if not procs:
            print("  (no recorded daemons — company not started, or already stopped)")
        for p in procs:
            role = p.get("role", "unknown")
            pid = p.get("pid")
            restarts = p.get("restart_count", 0)
            if p.get("dead"):
                all_ok &= _check(
                    f"  daemon {role}", False,
                    f"DEAD — {p.get('dead_reason', 'restart budget exhausted')} "
                    f"(pid={pid}, restarts={restarts}); run: hive stop && hive start")
            elif _pid_alive(pid):
                all_ok &= _check(f"  daemon {role}", True,
                                 f"pid={pid} restarts={restarts}")
            else:
                # Not alive but not marked dead: either inside the supervisor's
                # ~30s restart window, or the supervisor itself is gone.
                # Warn, don't fail — a live supervisor will restart it.
                print(f" \033[33m⚠\033[0m {('daemon ' + role):<35} "
                      f"pid={pid} not running (restarts={restarts}) — supervisor "
                      f"restarts dead daemons up to HIVE_MAX_RESTARTS per session")

    print()
    if all_ok:
        print("\033[32mAll checks passed.\033[0m")
    else:
        print("\033[31mSome checks failed. See output above.\033[0m")
    return all_ok
