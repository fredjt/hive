# Contributing to Hive

Thanks for contributing. Hive is developed in the open; this page is the
short version of the rules the codebase itself enforces. The full guide
for working in the tree lives in [AGENTS.md](AGENTS.md) — read it first if
you haven't, because every change below must satisfy it.

## Ground rules

1. **Every change needs a test.** No feature or bugfix is complete without
   at least one test that fails on the old behavior and passes on the new.
   The test is what makes the fix real — it is what future CI runs to prove
   the regression stays fixed.
2. **The full suite must stay green.** Every commit you push should pass
   `python -m pytest tests/` — if it doesn't, the change isn't done.
3. **Write code like the tree already is.** Each key directory has its own
   `AGENTS.md` with the conventions for that module; follow it instead of
   inventing a new style.

## Setting up

```bash
# Dependencies (runtime + test tooling)
pip install -r packaging/requirements.txt
pip install -r packaging/requirements-dev.txt
```

Python 3.10+ is required. From the repo root:

```bash
# Full suite, serial
python -m pytest tests/ -v

# Full suite, parallel (~4x faster; pytest-xdist)
python -m pytest tests/ -n 8

# One file while iterating
python -m pytest tests/test_bash_scripts.py -v
```

For long sessions, `ptw` (pytest-watch) re-runs the suite on every file
change:

```bash
ptw --watch lib/ -- tests/
```

## Fixing a bug

1. Write the regression test first — it must fail against current code.
2. Fix the bug.
3. Watch the test flip green.
4. Run the full suite; nothing else may break.
5. Commit the test and the fix together.

## Adding a feature

1. Write acceptance tests describing the expected behavior.
2. Implement until they pass.
3. Add edge-case tests for the error paths.
4. Run the full suite.
5. Update `share/docs/` if the feature is user-facing.

## Where tests live

| What you changed | Test file |
|---|---|
| Bash scripts (`lib/tools/*.sh`) | `tests/test_bash_scripts.py` |
| CLI commands | `tests/test_cli.py`, `tests/test_commands.py` |
| MCP tools & server | `tests/test_mcp_tools.py` |
| Python modules | `tests/test_integration.py` |
| Layout, docs, packaging | `tests/test_dashboard.py` |
| JSON structures, config | `tests/test_json_structures.py` |
| Shell/temp-dir behavior | `tests/test_shell.py` |
| Agent prompts | `tests/test_agent_prompts.py` |

## Per-directory guides

The tree documents itself; the root [AGENTS.md](AGENTS.md) is the map:

- [lib/AGENTS.md](lib/AGENTS.md) — cross-module map, data flow
- [lib/core/AGENTS.md](lib/core/AGENTS.md) — agent-spawn, orchestrator, init
- [lib/tools/AGENTS.md](lib/tools/AGENTS.md) — bash script conventions
- [lib/hive_mcp/tools/AGENTS.md](lib/hive_mcp/tools/AGENTS.md) — MCP tool modules
- [lib/hive_cli/AGENTS.md](lib/hive_cli/AGENTS.md) — CLI dispatcher
- [lib/agents/AGENTS.md](lib/agents/AGENTS.md) — prompt architecture
- [lib/dashboard/AGENTS.md](lib/dashboard/AGENTS.md) — Node.js SPA
- [lib/templates/AGENTS.md](lib/templates/AGENTS.md) — templates & publishers
- [packaging/AGENTS.md](packaging/AGENTS.md) — installer & build
- [share/docs/AGENTS.md](share/docs/AGENTS.md) — documentation corpus

## Checklist before you commit

- [ ] New test added (or existing test updated) covering the change
- [ ] Full suite passes: `python -m pytest tests/`
- [ ] No regressions — previously passing tests still pass
- [ ] Commit message says *what* and *why*, not just *how*
- [ ] No secrets, tokens, or local state in the diff — CI runs a secret
      scan (gitleaks) and a history-scrub guard on every commit; if you
      accidentally commit a credential, report it privately per
      [SECURITY.md](SECURITY.md) and fix history with your maintainer.
