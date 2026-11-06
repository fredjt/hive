"""D1: the docs stop lying about a per-project `.hive/` scaffold.

The release the live company ran through documented a per-project
tweakable scaffold at <project>/.hive ("created by hive init via
scaffold-extract") that NO code ever created or resolved: HIVE_ROOT is
the global install and bin/hive sets it to the install dir. This release
chose the release-goal's docs-rewrite branch — primer, agent prompts,
and every AGENTS.md now say the install is the single root, read-only
from inside agents. These tests pin that truth so the fiction cannot
quietly return in a later prompt or doc edit.
"""
import glob
import os
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

# Agent-facing corpus: everything an agent or a repo contributor reads.
DOC_FILES = sorted(
    glob.glob(str(REPO / "lib" / "agents" / "**" / "*.md"), recursive=True)
    + glob.glob(str(REPO / "lib" / "templates" / "**" / "*.md"), recursive=True)
    + [str(REPO / "AGENTS.md"),
       str(REPO / "lib" / "hive_cli" / "AGENTS.md"),
       str(REPO / "lib" / "hive_cli" / "commands" / "AGENTS.md"),
       str(REPO / "lib" / "core" / "AGENTS.md"),
       str(REPO / "lib" / "tools" / "AGENTS.md"),
       str(REPO / "lib" / "hive_mcp" / "tools" / "AGENTS.md")]
)

# Affirmative path claims of the old fiction: a .hive tree INSIDE the
# project. (A bare negation — "no per-project .hive/ scaffold" — is fine.)
FICTION_PATTERNS = ["PROJECT_ROOT/.hive", "<project>/.hive", "project/.hive/"]

# Commands and scripts the fiction invented but the code never shipped.
PHANTOM_NAMES = ["scaffold-extract.sh", "scaffold-update.sh",
                 "hive scaffold", "scaffold.py"]


def _assert_not_in(pattern, files, what):
    hits = []
    for f in files:
        text = Path(f).read_text()
        for i, line in enumerate(text.splitlines(), 1):
            if pattern in line:
                hits.append(f"{os.path.relpath(f, REPO)}:{i}: {line.strip()[:80]}")
    assert not hits, f"{what} still present:\n" + "\n".join(hits)


def test_docs_never_claim_a_project_local_hive_tree():
    _assert_not_in("PROJECT_ROOT/.hive", DOC_FILES, "$PROJECT_ROOT/.hive")
    _assert_not_in("<project>/.hive", DOC_FILES, "<project>/.hive")


def test_no_phantom_scaffold_commands_or_scripts():
    code_files = (glob.glob(str(REPO / "lib" / "**" / "*.py"), recursive=True)
                  + glob.glob(str(REPO / "lib" / "**" / "*.sh"), recursive=True)
                  + [str(REPO / "bin" / "hive")])
    for name in PHANTOM_NAMES:
        _assert_not_in(name, code_files + DOC_FILES, name)


def test_primer_documents_the_install_root_and_it_resolves():
    primer = (REPO / "lib" / "agents" / "_FRAMEWORK_PRIMER.md").read_text()
    assert "global hive install" in primer, "primer must name HIVE_ROOT as the global install"
    assert "~/.local/share/hive" in primer
    # The primer's documented path form must actually resolve against the
    # install: an agent typing it gets an executable, not a missing file.
    assert (REPO / "lib" / "tools" / "ticket-create.sh").exists()
    assert os.access(REPO / "lib" / "tools" / "ticket-create.sh", os.X_OK)


def test_doctor_does_not_label_the_install_a_scaffold():
    doctor = (REPO / "lib" / "hive_cli" / "commands" / "doctor.py").read_text()
    assert "scaffold/{sub}" not in doctor
    assert "root/{sub}" in doctor


def test_dashboard_does_not_resolve_a_project_hive():
    server = (REPO / "lib" / "dashboard" / "server.js").read_text()
    assert "'.hive'" not in server and '".hive"' not in server


def test_init_steps_are_sequential():
    init = (REPO / "lib" / "core" / "init.sh").read_text()
    steps = [int(m) for m in re.findall(r"^# (\d+)\. ", init, re.M)]
    assert steps == list(range(1, len(steps) + 1)), (
        f"init.sh step numbers must be sequential 1..N (the 4 -> 6 gap was "
        f"the phantom extract step): {steps}")
