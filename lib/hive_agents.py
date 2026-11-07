"""Agent prompt assembly — the one place VP prompts are composed (G7).

The fixed-six department VPs are stored as FRAGMENTS: `vp-<dept>.md` is
just the title line + the "<Dept>-specific scope" section. Every spawn
path composes the real prompt from the shared base (`vp-dept.md`) and
the fragment, so a cross-cutting base change is a 1-file edit instead of
a 7-file sweep:

    <fragment title line>
    <base body up to and including the seam line>
    <blank line + fragment scope section>
    <base remainder after the seam>

The seam is the "### Then exit" paragraph
("The sprint archives automatically; next sprint's stage 1 (ceo-plan)
runs."); the scope section is inserted after it. Dynamic departments
(HR-created depts beyond the fixed six) get the base verbatim —
`vp-dept.md` is itself a live prompt and must stay complete.

Callers:
- lib/tools/load-agent-prompt.sh  (sprint-lifecycle spawn path, bash)
- lib/hive_mcp/tools/agents.py    (agent_run_vp MCP tool)
"""

from __future__ import annotations

import os
from pathlib import Path

#: The departments whose VP prompts are composed from a fragment.
FIXED_DEPTS = ("hr", "it", "rd", "marketing", "legal", "finance")

#: The base line after which the dept scope section is inserted.
_SEAM_MARKER = "next sprint's stage 1 (ceo-plan) runs."


def _lib_dir() -> Path:
    """The lib/ directory this module belongs to.

    HIVE_ROOT / HIVE_INSTALL_DIR win when set (installed context); the
    __file__ fallback covers running from a source checkout. (G9 will
    centralize this derivation in one _paths module.)
    """
    for var in ("HIVE_ROOT", "HIVE_INSTALL_DIR"):
        val = os.environ.get(var)
        if val:
            p = Path(val) / "lib"
            if p.is_dir():
                return p
    return Path(__file__).resolve().parent


def agents_dir() -> Path:
    return _lib_dir() / "agents"


def compose_dept_vp(dept: str, lib_dir: Path | None = None) -> str:
    """Render the VP prompt for `dept`.

    Fixed six with a fragment present -> composed render. Fixed six with
    the fragment missing, or any other (dynamic) dept -> the base as-is.
    A fragment whose first line is not a markdown title is treated as
    malformed and the base is returned (the structural test catches this
    in the suite; a field run keeps working on the base).
    """
    lib = lib_dir or _lib_dir()
    base_path = lib / "agents" / "vp-dept.md"
    base = base_path.read_text()
    if dept not in FIXED_DEPTS:
        return base
    frag_path = lib / "agents" / f"vp-{dept}.md"
    if not frag_path.is_file():
        return base
    frag_lines = frag_path.read_text().splitlines(keepends=True)
    if not frag_lines or not frag_lines[0].startswith("#"):
        return base
    base_lines = base.splitlines(keepends=True)
    seam = next((i for i, line in enumerate(base_lines) if _SEAM_MARKER in line), None)
    if seam is None:
        return base
    scope = frag_lines[1:]
    while scope and not scope[0].strip():
        scope.pop(0)
    out = [frag_lines[0]]
    out.extend(base_lines[1:seam + 1])
    out.append("\n")
    out.extend(scope)
    out.extend(base_lines[seam + 1:])
    return "".join(out)
