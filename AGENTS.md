# AGENTS.md — Hive

This file is the single guide for working on the **Hive** project: what it
is, how it is built, and how to develop it (add features, fix bugs, run
tests, ship changes). If you are an AI agent or a human contributor, follow
these guidelines to keep the codebase healthy and regression-free.

## What is Hive

Hive is a standalone framework for orchestrating a swarm of AI agents as a
virtual company on a software project. It runs as its own application. Its
agent runtime is the **built-in agent harness** (`lib/core/agent-harness.py`):
plain Python that calls LLM provider APIs directly and reaches the hive MCP
server over localhost HTTP. No external agent CLI is installed, invoked, or
required anywhere.

## Directory layout

```
bin/hive                              CLI launcher (bash)
lib/                                  Runtime core (see lib/AGENTS.md for details)
  hive_state/                         The .company/ state layer (flock + atomic + schema)
  hive_mcp/                           MCP server (Python, FastMCP)
  hive_cli/                           CLI dispatcher (Python)
  core/                               Foundational bash scripts (agent-spawn, init)
  tools/                              ~80 bash scripts (~11.2k lines)
  agents/                             Agent prompt files
  dashboard/                          Node.js web dashboard (modular SPA, static/ frontend)
  templates/                          GitHub PR template, CI/release workflows
packaging/                            Install scripts, build, metadata
tests/                                pytest test suite
docs/                                 Developer notes
share/docs/                           Human-readable documentation
etc/config.defaults.toml              Default configuration
```

Each key subdirectory has its own `AGENTS.md` with deeper detail. See the
**See also** section at the bottom.

## Key architecture concepts

### One install, two env names

- **HIVE_INSTALL_DIR** (~/.local/share/hive): Global install — venv, MCP
  server, CLI, dashboard. Read-only from inside agents.
- **HIVE_ROOT**: The same global install, seen from the agent side —
  lib/agents (prompts), lib/tools (~80 scripts), lib/templates,
  lib/core (spawn + harness). Defaults to HIVE_INSTALL_DIR (bin/hive sets
  both). Shared by every project on the machine; read-only from inside
  agents. There is NO per-project copy — `hive init` does not create one.
  Per-project state lives in `.company/`.

### Runtime paths

- **PROJECT_ROOT**: The project directory containing `.company/`
- **COMPANY_DIR** ($PROJECT_ROOT/.company): Live runtime state — tickets,
  chat, roster, sprint state, logs. Agents read freely but MUST NOT write
  directly; all writes go through MCP tools or bash scripts.

### Two-transport MCP server

The MCP server (`lib/hive_mcp/server.py`) supports two transports:
- **http** (port 7891): Used by the dashboard, the CLI, and spawned agents
  (the harness calls tools over localhost HTTP).
- **stdio**: A plain programmatic transport — run the server as a child
  process from a script, test, or interactive session and talk JSON-RPC
  over its stdin/stdout.

### Agent runtime (the harness)

Every spawned agent goes through `lib/core/agent-spawn.sh`, which execs
`lib/core/agent-harness.py`. The harness runs a conversation loop against
the configured LLM provider (HIVE_* env vars and the `[llm]` config section
drive provider/model/base_url/api_key), executes `bash:` blocks with safety
limits, calls MCP tools from `mcp:` blocks over HTTP, and writes
stream-json events that the dashboard replays.

### Sprint-staged lifecycle

The round-runner serially walks through stages:
1. ceo-plan → 2. vp-plan → 2.5 sprint-fill → 3. tdd-red → 4. tdd-green →
5. verify → (loop back to 3) → 6. build-and-test → 7. release-verify →
8. release-publish → (loop back to 6a) → 9. vp-review → archive → next
sprint

### Important conventions

- Ticket statuses: `backlog` → `sprint-current` → `in-progress` →
  `in-review` → `done` → `archive`
- Ticket types: `feature`, `bug`, `improvement`, `chore`,
  `infrastructure`, `research`
- Priorities: `critical`, `high`, `medium`, `low`
- Agent roles: CEO, VPs (per dept), ICs (engineer, tester, infra, admin),
  Verification IC, Release IC
- Worktrees: per-ticket at `$PROJECT_ROOT/.worktrees/$TID` on branch
  `feature/$TID`
- The `.company/` directory is live state treated as a database — agents
  read freely, writes go through MCP tools or scripts only
- **Derived state**: `roster.json` and per-dept `status.json` are rebuilt
  every 60s from ticket directories, VP log mtimes, and git worktrees —
  nothing else writes them

## Common commands

### Installation (from extracted archive)
```bash
bash packaging/install.sh --copy-to ~/.local/share/hive
# Verify:
hive doctor
```

### Per-project usage
```bash
cd /path/to/project
hive init . --brief "Project description"
hive                     # Launches dashboard at http://localhost:3456
hive start               # Starts all daemons (MCP server, round-runner, pulse, orphan-recovery, roster/status rebuild)
hive status              # Show derived department status
hive stop                # Kill all daemon processes
hive ticket list backend # List tickets
```

### MCP server (standalone)
```bash
# HTTP transport (default port 7891)
python -m hive_mcp.server --transport http --host 127.0.0.1 --port 7891
# stdio transport (plain programmatic consumers)
python -m hive_mcp.server --transport stdio
```

### CLI (from source, without install)
```bash
PYTHONPATH=lib python -m hive_cli.main --help
```

### Dashboard
```bash
# From source (Node.js, no build step)
node lib/dashboard/server.js --dir .company --port 3456
```

## Configuration

- Defaults: `etc/config.defaults.toml`
- Overrides: `~/.config/hive/config.toml` — the `[llm]` section is the
  canonical LLM layer; every resolver reads it.
- One-shot runs: export the LLM env vars (`HIVE_*`) in your
  interactive shell — they override the config for that shell's
  processes only (see `share/docs/LLM-PROVIDERS.md`).
- Environment (for daemons): `~/.config/hive/env.sh` — shell-level
  daemon env only (PATH additions, GITHUB_TOKEN for publishers that
  don't use `gh auth login`), so daemons spawned via nohup/systemd
  inherit it without interactive shell rc files. It must NOT carry LLM
  settings — doctor's D1 check warns on them; they live in `[llm]`
  in `config.toml`.

## Python dependencies

`packaging/requirements.txt` carries the floor pins
(mcp, fastapi, uvicorn[standard], httpx, tomli). The install's Python
environment is pinned in `packaging/requirements.lock.txt` (T1, 0.14 —
exact pins, marker-aware): install.sh installs from the lock, the build
self-verifies it, and doctor cross-checks the venv against it. Refresh
after a requirements.txt change: `bash packaging/refresh-venv-lock.sh`.

---

## Quick Start

```bash
# From the repo root
cd hive

# Run the full test suite — PRIMARY loop: parallel (needs pytest-xdist;
# the speedup is host-dependent — see the "xdist worker contract"
# section below). The suite is xdist-safe by construction, so this is
# the recommended dev loop; measure the split on your host with:
#   time python -m pytest tests/ -q -n 8   vs   time python -m pytest tests/ -q
python -m pytest tests/ -n 8

# Debug mode: serial, verbose — single-worker runs while writing a
# regression test or reproducing a worker-specific failure
python -m pytest tests/ -v

# Run a subset
python -m pytest tests/test_bash_scripts.py -v
python -m pytest tests/test_integration.py -v

# Run with coverage
python -m pytest tests/ --cov=lib/hive_cli --cov=lib/hive_mcp --cov-report=term-missing
```

---

## Project Structure

```
bin/hive                              CLI launcher (bash)
lib/                                  Runtime core
  hive_state/                         The .company/ state layer (flock + atomic + schema)
  hive_mcp/                           MCP server (Python, FastMCP)
    server.py                         Main entry point, two-transport (stdio + HTTP)
    tools/                            MCP tool modules (Python→bash bridge)
  hive_cli/                           CLI dispatcher (Python)
    main.py                           Subcommand routing
    commands/                         Per-subcommand handlers
  core/                               Foundational bash scripts (agent-spawn, init)
  tools/                              ~80 bash scripts (~11.2k lines)
  agents/                             Agent prompt files
  dashboard/                          Node.js web dashboard (modular SPA, static/ frontend)
  templates/                          GitHub PR template, CI/release workflows
packaging/                            Install scripts, build, metadata
tests/                                pytest test suite
docs/                                 Developer notes
share/docs/                           Human-readable documentation
etc/config.defaults.toml              Default configuration
```

---

## Development Workflow

### 1. Every Change Needs a Test

**Rule: No feature or bugfix is complete without at least one test.**

Before writing code, write the test that should *fail* for the current broken state and *pass* after the fix. This ensures:

- The bug/feature is captured as an automated check
- Future regressions are caught immediately by CI
- The intent of the change is documented in executable form

**Where to put tests:**

| What you changed | Test file |
|---|---|
| Bash scripts (`lib/tools/*.sh`) | `tests/test_bash_scripts.py` |
| CLI commands (`hive ticket`, `hive start`, etc.) | `tests/test_cli.py`, `tests/test_commands.py` |
| MCP tools & server | `tests/test_mcp_tools.py` |
| Python modules (`hive_cli/`, `hive_mcp/`) | `tests/test_integration.py` |
| Documentation, packaging, directory layout | `tests/test_dashboard.py` |
| JSON structures, config files | `tests/test_json_structures.py` |
| Shell scripts, temp-dir isolation | `tests/test_shell.py` |
| Agent prompts & agent-related changes | `tests/test_agent_prompts.py` |

### 2. Writing Tests

#### Bash Script Tests

Use subprocess to call the script and assert on exit code, stdout, stderr:

```python
def test_script_has_valid_stages():
    install_dir = get_install_dir()
    script = install_dir / "lib" / "tools" / "sprint-state.sh"
    result = subprocess.run(
        ["bash", str(script), "stages"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0
    stages = result.stdout.strip().split()
    assert "backlog" in stages
    assert "sprint-current" in stages
```

#### Python/Integration Tests

Use the `tmp_project_dir` and `tmp_company_dir` fixtures to create isolated environments:

```python
def test_ticket_create_moves_to_backlog(tmp_project_dir):
    company_dir = tmp_project_dir / ".company"
    result = subprocess.run(
        ["hive", "ticket", "create", "test-ticket", "--type", "feature"],
        capture_output=True, text=True, timeout=30,
        cwd=str(tmp_project_dir),
        env={**os.environ, "COMPANY_DIR": str(company_dir)},
    )
    assert result.returncode == 0
    ticket_file = company_dir / "tickets" / "test-ticket" / "ticket.json"
    assert ticket_file.exists()
    data = json.loads(ticket_file.read_text())
    assert data["status"] == "backlog"
```

#### Agent Prompt Tests

Verify that agent prompt files contain required sections:

```python
def test_agent_prompt_has_required_sections():
    install_dir = get_install_dir()
    agent_file = install_dir / "lib" / "agents" / "qa.md"
    content = agent_file.read_text()
    assert "ROLE:" in content
    assert "TOOLS:" in content
    assert "WORKFLOW:" in content
```

### 3. Running Tests During Development

```bash
# Run all tests (fast feedback loop)
python -m pytest tests/ -v

# Run in parallel (needs pytest-xdist). The speedup is HOST-DEPENDENT:
# on the 0.11 reference box the full suite is ≈155s serial and ≈155s
# with -n 8 (the subprocess-heavy lifecycle tests dominate, so xdist
# buys nothing there), while a many-core box goes ≈760s -> ≈160s.
# -n 8 is the sweet spot; more workers don't help.
# The suite is xdist-safe by construction (see the "xdist worker
# contract" in Test Suite Architecture): ephemeral ports, per-test
# temp dirs, in-process MCP servers, no in-process reload of lib
# modules.
python -m pytest tests/ -n 8

# Single test file while developing
python -m pytest tests/test_bash_scripts.py::TestSprintStateScript -v

# Verbose output with tracebacks for failing tests
python -m pytest tests/test_integration.py -v --tb=long
```

**Watch loop (`ptw`).** For long development sessions, `pytest-watch`
re-runs the suite (or a subset) automatically whenever a watched file
changes — no manual re-run between edits:

```bash
pip install -r packaging/requirements-dev.txt   # or: pip install pytest-watch

# Watch the runtime core, run the full suite on each change
ptw --watch lib/ -- tests/

# Narrower loop: watch one module, run only its test file
ptw --watch lib/tools/ -- tests/test_bash_scripts.py

# Filter to specific tests while iterating on one bug
ptw --watch lib/ -k ticket_move
```

Notes:
- `ptw` runs the *serial* suite; add `-n 8` after the `--` only if you
  want the parallel run inside the loop (usually unnecessary for a subset).
- If `ptw` is unavailable, the equivalent one-liner is
  `watch -n 2 'python -m pytest tests/ -q -n 8'`.

### 4. Fixing Bugs

When a bug is found:

1. **Write a regression test first** — it should fail with the current code
2. **Fix the bug** in the source
3. **Verify the test passes**
4. **Run the full suite** to ensure no regressions elsewhere
5. **Commit both the test and the fix together**

```bash
# Example workflow
python -m pytest tests/test_bash_scripts.py::TestCoreScripts::test_bin_hive_is_executable -v  # FAILS
# ... fix lib/tools/sprint-state.sh ...
python -m pytest tests/test_bash_scripts.py::TestCoreScripts::test_bin_hive_is_executable -v  # PASSES
python -m pytest tests/ -v  # ALL PASS
git add tests/ lib/
git commit -m "fix: sprint-state.sh path issue + regression test"
```

### 5. Adding Features

When adding a new feature:

1. **Write acceptance tests** that describe the expected behavior
2. **Implement the feature** to make those tests pass
3. **Add unit tests** for edge cases and error paths
4. **Run the full suite** — all tests must pass
5. **Update documentation** in `share/docs/` if user-facing

---

## Test Suite Architecture

### Fixtures (`tests/conftest.py`)

Shared fixtures provide isolated test environments:

- **`tmp_project_dir`** — Creates a temporary project directory and points `HIVE_INSTALL_DIR` at the install (no per-project scaffold — the runtime always resolves from the install)
- **`tmp_company_dir`** — Creates a minimal `.company/` workspace inside the test project
- **`get_install_dir()`** — Returns the repo root (where `lib/`, `bin/`, etc. live)

### xdist worker contract

The suite is xdist worker-safe *by construction* — not by rerunning
until the unlucky draw stops. The 0.10.0 matrix lost three flakes in
one week (in-process `importlib.reload()` replacing `ShellResult` with
a new class object; a wall-clock second boundary crossed between two
move paths; a stage flag observable before the manifest it commits to).
Each rule below is pinned by a static guard in `tests/test_xdist_contract.py`:

- **No in-process `importlib.reload()` of `lib/` modules in the test
  process.** A reload re-executes the module and replaces shared
  objects with NEW class objects while other test modules hold the old
  bindings — on the worker that ran both, `isinstance()` fails.
  Subprocess isolation is the pattern (see
  `tests/test_hive_paths.py`): fresh interpreter, same env contract.
- **Wall-clock comparisons go through `conftest.normalize_clock()`.**
  Two invocations a few ms apart cross a second boundary on ~5% of
  runs — the contract is identical STATE, not identical INSTANT. No
  hand-rolled normalizers in test modules.
- **State observed across processes has a single commit point.** Write
  the manifest/flag BEFORE the stage flip, so an observer that sees the
  flip is guaranteed to see the flag (the idle transition writes the
  manifest first, stage second; `stage` is the commit point).
- **Ordered classes with a class-scoped fixture are pinned to one
  worker (`@pytest.mark.xdist_group`) under the loadgroup strategy.**
  The default `--dist=load` sends the pending queue in consecutive
  chunks (up to `--maxschedchunk`, 8 by default), so an ordered
  end-to-end class with more tests than a chunk can be split across
  two workers; each worker then instantiates the class-scoped fixture
  in its OWN temp dir, and the test that reads state mutated by an
  earlier same-class test sees the unmutated fixture (0.18 gate-2
  caught `TestTicketHumanFace` — "5 ticket(s) in rd" read as 3, the
  mutations having landed on another worker). `pytest.ini` runs
  `--dist=loadgroup`: per-test scheduling for unmarked tests, group-
  unit scheduling for marked ones. Every class whose class-scoped
  fixture carries ordered state carries the mark; a new one without it
  fails `tests/test_xdist_contract.py::TestXdistGroupContract`.

### Test Categories

| Category | File | What it checks |
|---|---|---|
| Bash scripts | `test_bash_scripts.py` | Script existence, executability, valid stages, usage messages |
| CLI | `test_cli.py` | Command routing, help output, subcommand availability |
| Commands | `test_commands.py` | Subcommand handlers (`ticket`, `init`, `start`, etc.) |
| Dashboard/docs | `test_dashboard.py` | Directory layout, pyproject.toml, LICENSE, docs existence |
| Integration | `test_integration.py` | Full init flow, ticket lifecycle, sprint state, imports |
| JSON structures | `test_json_structures.py` | Config file validity, roster/status JSON schema |
| MCP tools | `test_mcp_tools.py` | Tool definitions, parameter validation |
| Shell | `test_shell.py` | Temp-dir isolation, script behavior in clean environments |
| Agent prompts | `test_agent_prompts.py` | Prompt sections, role definitions, tool references |

### pytest Configuration (`pytest.ini`)

```ini
[tool:pytest]
pythonpath = hive/lib
```

The `pythonpath` ensures that `hive_cli` and `hive_mcp` modules are importable during tests. Tests also inject the lib path directly in `conftest.py` for robustness.

---

## Common Pitfalls & Fixes

### Wrong install directory path

**Bug:** `get_install_dir()` returned `Path(__file__).resolve().parent.parent / "hive"` (an extra `/hive` subdirectory).

**Fix:** Return the repo root directly — `lib/`, `bin/`, and `tests/` all live at the same level.

```python
def get_install_dir():
    return Path(__file__).resolve().parent.parent  # Repo root, not /hive
```

### Hardcoded paths in tests vs source layout

**Bug:** Tests referenced `hive/bin/hive` but after cloning to a git repo, the structure is flat — `bin/hive`.

**Fix:** Always use relative paths from `get_install_dir()`:

```python
install_dir = get_install_dir()
script = install_dir / "lib" / "tools" / "sprint-state.sh"
```

### Subprocess calls missing env/cwd

**Bug:** `subprocess.run()` without `cwd` or `env` parameters failed when `.company/` existed in the working directory.

**Fix:** Always pass explicit `cwd` and `env`:

```python
result = subprocess.run(
    ["hive", "ticket", "create", "test"],
    capture_output=True, text=True, timeout=30,
    cwd=str(tmp_project_dir),
    env={**os.environ, "COMPANY_DIR": str(company_dir)},
)
```

### f-strings with bash arrays

**Bug:** Python f-strings tried to expand `$STAGES` as a Python variable.

**Fix:** Escape the dollar sign or use raw strings:

```python
# Wrong: f"The stages are $STAGES"
# Right:
assert "$STAGES" in result.stdout or "stages" in result.stdout.lower()
```

---

## CI / Continuous Integration

Tests run automatically on every push/PR. The test matrix is split
by risk (S4): **one version (3.12) carries the full suite**; every
supported version (3.10, 3.13) carries its **version-sensitive
subset** — the config-load/tomli-fallback tests
(`tests/test_json_structures.py`, `tests/test_llm_providers.py`,
`tests/test_commands.py::TestConfigSetUpsert`,
`tests/test_commands.py::TestConfigIntegrity`) + a CLI smoke
(`tests/test_cli.py`) + an import smoke. The version-
sensitive surface is PINNED by
`tests/test_integration.py::TestTomliSurfaceGuard`: a new
`import tomli` / `tomllib` outside the pinned files fails the guard
until the ci.yml subset is updated with it. The pipeline:

1. **Clone the repo**
2. **Run the matrix** — full suite on 3.12; the subset on 3.10 + 3.13 (plus bash syntax, ruff, dashboard, and gitleaks jobs)
3. **Report failures** — each failing test includes the file, line, and traceback

If a PR fails CI, fix the underlying bug (not just the test). The test exists to prevent that exact regression.

---

## Checklist for Every Commit

Before committing any change:

- [ ] **New test added or existing test updated** to cover the change
- [ ] **Full test suite passes**: `python -m pytest tests/ -v`
- [ ] **No regressions**: all previously passing tests still pass
- [ ] **Commit message is descriptive**: explains *what* and *why*, not just *how*

---

## See Also

- [`lib/AGENTS.md`](lib/) — Cross-module map, data flow, serialization points
- [`lib/core/AGENTS.md`](lib/core/) — Foundational scripts: agent-spawn, orchestrator, init
- [`lib/tools/AGENTS.md`](lib/tools/) — Bash script conventions
- [`lib/hive_mcp/tools/AGENTS.md`](lib/hive_mcp/tools/) — MCP tool modules
- [`lib/hive_cli/AGENTS.md`](lib/hive_cli/) — CLI dispatcher, command handlers
- [`lib/agents/AGENTS.md`](lib/agents/) — Agent prompt architecture, role hierarchy
- [`lib/dashboard/AGENTS.md`](lib/dashboard/) — Single-file Node.js SPA architecture
- [`lib/templates/AGENTS.md`](lib/templates/) — GitHub PR template, CI workflows, publishers
- [`packaging/AGENTS.md`](packaging/) — Installer behavior, build, uninstall
- [`share/docs/AGENTS.md`](share/docs/) — Documentation corpus, cross-references
