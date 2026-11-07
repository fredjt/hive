"""G9: install-path resolution — the one place HIVE_ROOT is derived.

Before G9 every Python module re-derived the install root with its own
parents[N] chain (14 sites; the AGENTS.md pitfall section records the
extra-/hive-subdirectory bug this produced). lib/hive_paths.py is now
the single implementation; these tests pin the resolution order and
the no-more-derivation contract.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))

import hive_paths  # noqa: E402


# ---------------------------------------------------------------------------
# Resolution order
# ---------------------------------------------------------------------------

class TestInstallRootResolution:
    def test_env_hive_root_wins(self, monkeypatch):
        monkeypatch.setenv("HIVE_ROOT", str(REPO))
        monkeypatch.setenv("HIVE_INSTALL_DIR", "/nonexistent-install")
        assert hive_paths.install_root() == REPO

    def test_env_hive_install_dir_fallback(self, monkeypatch):
        monkeypatch.delenv("HIVE_ROOT", raising=False)
        monkeypatch.setenv("HIVE_INSTALL_DIR", str(REPO))
        assert hive_paths.install_root() == REPO

    def test_derived_fallback(self, monkeypatch):
        monkeypatch.delenv("HIVE_ROOT", raising=False)
        monkeypatch.delenv("HIVE_INSTALL_DIR", raising=False)
        # in-repo run: this file lives at REPO/lib/hive_paths.py
        assert hive_paths.install_root() == REPO

    def test_bad_env_falls_through(self, tmp_path, monkeypatch):
        # an env root without lib/ is not an install root — fall through
        monkeypatch.setenv("HIVE_ROOT", str(tmp_path))
        monkeypatch.setenv("HIVE_INSTALL_DIR", str(REPO))
        assert hive_paths.install_root() == REPO

    def test_lib_and_subdir_helpers(self, monkeypatch):
        monkeypatch.setenv("HIVE_ROOT", str(REPO))
        assert hive_paths.lib_dir() == REPO / "lib"
        assert hive_paths.agents_dir() == REPO / "lib" / "agents"
        assert hive_paths.tools_dir() == REPO / "lib" / "tools"
        assert hive_paths.core_dir() == REPO / "lib" / "core"
        assert hive_paths.version_file() == REPO / "VERSION"


# ---------------------------------------------------------------------------
# No-derivation contract
# ---------------------------------------------------------------------------

class TestNoMoreParentsDerivation:
    @pytest.mark.parametrize("name,attr", [
        ("hive_mcp.shell", "HIVE_ROOT"),
        ("hive_cli.mcp_client", "HIVE_ROOT"),
        ("hive_cli.commands.killall", "HIVE_ROOT"),
        ("hive_cli.commands.config", "HIVE_ROOT"),
        ("hive_cli.commands.doctor", "HIVE_ROOT"),
        ("hive_cli.commands.dashboard", "HIVE_ROOT"),
        ("hive_cli.commands.restart", "HIVE_ROOT"),
        ("hive_cli.commands.company", "_HIVE_ROOT"),
    ])
    def test_module_root_matches_hive_paths(self, name, attr):
        """Run in a subprocess: reloading these modules in the test
        process re-executes them and replaces shared objects (e.g.
        hive_mcp.shell.ShellResult) with NEW class objects, while other
        test modules still hold the old bindings — isinstance() checks
        then fail. That was the xdist flake behind
        test_shell.py::TestRun failures on the 3.10 CI job. A subprocess
        gives the module a fresh, isolated import under the same env
        contract: HIVE_ROOT set, HIVE_INSTALL_DIR unset."""
        code = (
            "import sys, importlib\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "mod = importlib.import_module(" + repr(name) + ")\n"
            "print(getattr(mod, " + repr(attr) + "))\n"
        )
        env = {k: v for k, v in os.environ.items()
               if k not in ("HIVE_ROOT", "HIVE_INSTALL_DIR")}
        env["HIVE_ROOT"] = str(REPO)
        r = subprocess.run([sys.executable, "-c", code, str(REPO / "lib")],
                           capture_output=True, text=True, timeout=60,
                           env=env)
        assert r.returncode == 0, name + ": " + r.stderr[-400:]
        # The module's root attribute resolves to the same value
        # hive_paths.install_root() gives under the same env.
        assert Path(r.stdout.strip()) == REPO

    def test_no_parents_derivation_remains_in_lib_python(self):
        """The 14-site re-derivation is gone for good: no Python file
        under lib/ derives any path with parents[N]."""
        offenders = []
        for p in (REPO / "lib").rglob("*.py"):
            if "__pycache__" in str(p):
                continue
            if "parents[" in p.read_text(encoding="utf-8"):
                offenders.append(str(p.relative_to(REPO)))
        assert not offenders, f"parents[N] re-derivation remains: {offenders}"