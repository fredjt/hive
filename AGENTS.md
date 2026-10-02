# AGENTS.md — How Hive Is Developed

This file documents the development workflow for the **Hive** project: how to add features, fix bugs, run tests, and ship changes. If you are an AI agent or a human contributor, follow these guidelines to keep the codebase healthy and regression-free.

---

## Quick Start

```bash
# From the repo root
cd hive

# Run the full test suite
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
  hive_mcp/                           MCP server (Python, FastMCP)
    server.py                         Main entry point, two-transport (stdio + HTTP)
    tools/                            MCP tool modules (Python→bash bridge)
  hive_cli/                           CLI dispatcher (Python)
    main.py                           Subcommand routing
    commands/                         Per-subcommand handlers
  core/                               Foundational bash scripts (agent-spawn, init, hooks)
  tools/                              ~101 bash scripts (~13k lines)
  agents/                             Claude agent prompt files
  dashboard/                          Node.js web dashboard (single-file SPA)
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

# Watch mode — re-run on file changes
pip install pytest-watch
ptw --watch lib/ -- tests/

# Single test file while developing
python -m pytest tests/test_bash_scripts.py::TestSprintStateScript -v

# Verbose output with tracebacks for failing tests
python -m pytest tests/test_integration.py -v --tb=long
```

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

- **`tmp_project_dir`** — Creates a temporary project with a `.hive/` scaffold extracted from the install
- **`tmp_company_dir`** — Creates a minimal `.company/` workspace inside the test project
- **`get_install_dir()`** — Returns the repo root (where `lib/`, `bin/`, etc. live)

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

Tests run automatically on every push/PR. The pipeline:

1. **Clone the repo**
2. **Run `python -m pytest tests/ -v`** — all tests must pass
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

- [`CLAUDE.md`](CLAUDE.md) — Project overview, architecture, commands
- [`lib/CLAUDE.md`](lib/CLAUDE.md) — Cross-module map, data flow
- [`lib/tools/CLAUDE.md`](lib/tools/CLAUDE.md) — Bash script conventions
- [`lib/hive_mcp/tools/CLAUDE.md`](lib/hive_mcp/tools/) — MCP tool modules
- [`packaging/CLAUDE.md`](packaging/) — Installer, build process
