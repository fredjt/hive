# AGENTS.md

This file documents the `this directory` area of the Hive project — what it does, how it works, and the conventions for extending it.

## Purpose

Each `.py` file here is a handler for one or more `hive <subcommand>` entries. The dispatcher lives in `lib/hive_cli/main.py` — this directory contains only the per-subcommand logic.

## Handler pattern

Every command file exports:

- **`handle(args) -> int`** — the canonical handler, receives an `argparse.Namespace`, returns 0 for success or non-zero for failure. The result is passed to `sys.exit()`.
- **`handle_<action>(args) -> int`** — used when a subcommand has multiple actions (e.g. `ticket create`, `ticket get`). The dispatcher in `main.py` wires the `action` subparser to call the top-level `handle()`, which dispatches internally.

### Two transport styles

**Style A — MCP tool delegation** (most commands): Build a payload dict, call `mcp_client.call_tool("tool_name", payload)`, print the result as JSON.

```python
from hive_cli import mcp_client

def handle(args) -> int:
    cd = _company_dir(args)
    payload = {"dept": args.dept, "company_dir": cd}
    res = mcp_client.call_tool("ticket_create", payload)
    print(json.dumps(res, indent=2))
    return 0 if res.get("ok") else 1
```

**Style B — Direct script execution (process management ONLY).** 0.10 (G2) retired Style B for everything that mutates `.company/` state: no handler may shell out to a `lib/tools/*.sh` script to change company state — that goes through the MCP client (Style A) over the G1 state layer. The ONLY remaining direct script calls are true process management / scaffolding, which must bypass MCP because spawned agents were restart-looping the daemons via MCP calls:

- `company-start.sh`, `company-stop.sh`, `company-health.sh` (daemons)
- `hive-killall.sh` (process tree)
- `core/init.sh` / `core/init-workspace.sh` (scaffolding a fresh `.company/`)
- `dashboard/server.js` and `core/agent-harness.py` (probers)

`tests/test_commands.py::TestStyleBRetirement` enforces this allowlist: any new direct script call outside it fails the suite.

```python
import subprocess

proc = subprocess.Popen(
    ["bash", str(HIVE_ROOT / "lib" / "tools" / "company-start.sh"), project_root],
    env={**os.environ, "HIVE_ROOT": str(HIVE_ROOT)},
    ...
)
```

## Common conventions

- **Company dir resolution**: Prefer `_cd()` or `_company_dir(args)` helpers that check `Path.cwd() / ".company"` — they return `None` if the directory doesn't exist. Some commands accept an explicit `company_dir` argument; pass it as a payload key.
- **JSON output**: All commands that produce structured output use `json.dumps(result, indent=2)`.
- **Exit codes**: Return 0 on success, non-zero on failure. The dispatcher passes this to `sys.exit()`.
- **HIVE_ROOT / HIVE_INSTALL_DIR**: Resolve via `Path(__file__).resolve().parents[3]` as a fallback when environment variables are unset. `parents[3]` walks up: `commands/` → `hive_cli/` → `hive_cli` (parent dir) → `hive-<version>/` (install root).
- **Docstrings**: Every file has a module-level docstring describing its subcommands. Multi-action commands list them in the docstring.

## Adding a new subcommand

1. Create `lib/hive_cli/commands/<name>.py` with a `handle(args) -> int` function.
2. Import it in `lib/hive_cli/main.py` (add to the `from hive_cli.commands import (...)` line).
3. Add `_add_<name>(sub)` function that builds an `argparse` subparser and sets `p.set_defaults(_handler=commands.<name>.handle)`.
4. Call `_add_<name>(sub)` from `build_parser()`.

## Command inventory

| File | Subcommand(s) | Transport |
|------|--------------|-----------|
| `company.py` | `start`, `stop`, `status` | Script (start/stop), MCP (status) |
| `ticket.py` | `ticket create|get|list|move|claim|comment|approve` | MCP |
| `chat.py` | `chat send|read` | MCP |
| `agent.py` | `agent spawn-ceo|run-vp` | MCP |
| `round.py` | `round status|next|history|get|enqueue|advance|promote` | MCP |
| `gates.py` | `gates run|check|status` | MCP + direct filesystem read |
| `gh.py` | `gh status|issues|mirror|pr open|merge|reject` | MCP |
| `release.py` | `release start|status|list|publish|abort` | MCP (start/publish/abort removed) |
| `backlog.py` | `backlog prioritize` | MCP |
| `inbox.py` | `inbox file|process` | MCP |
| `config.py` | `config get|set|edit|show` | Filesystem (tomllib) |
| `llm.py` | `llm stats|tail` | Filesystem (log parsing) |
| `doctor.py` | `doctor` | Filesystem + MCP probe |
| `dashboard.py` | `dashboard` | Process management (starts MCP + Node) |
| `init.py` | `init` | Script (init.sh) |
| `tool.py` | `tool <name>`, `tools` | MCP |
| `killall.py` | `killall` | Script (hive-killall.sh) |
| `__init__.py` | — | Empty (namespace package marker) |

## The mcp_client module

The client (`lib/hive_cli/mcp_client.py`) auto-selects transport (HTTP → in-process; the SDK import is lazy, 0.20 T1). See `lib/hive_cli/AGENTS.md` for details.

## See also

- Parent `AGENTS.md` — Framework-level architecture, common commands
- `lib/AGENTS.md` — Cross-module data flow
- `lib/hive_cli/AGENTS.md` — CLI dispatcher, mcp_client, path resolution
