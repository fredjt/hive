# CLAUDE.md

This file provides guidance for working within the `lib/hive_cli/` module.

## Role

`hive_cli` is the Python CLI dispatcher for the Hive framework. It parses argparse subcommands and routes them to handler modules, which in turn call the MCP server (via `mcp_client`) to execute tools. It is the human-facing interface — the dashboard and spawned Claude agents use the MCP server directly.

## Structure

```
lib/hive_cli/
  main.py            argparse entry point — build_parser() + main()
  mcp_client.py      Async MCP client (HTTP + stdio transports) with sync wrapper
  __init__.py        (empty)
  commands/          One module per CLI subcommand
    company.py       start / stop / status
    ticket.py        create / get / list / move / claim / comment / approve
    init.py          workspace initialization (delegates to bash)
    dashboard.py     launches Node.js dashboard server
    chat.py          send / read messages
    agent.py         spawn-ceo / run-vp / verify
    gates.py         run / check / status
    backlog.py       prioritize
    inbox.py         file / process
    config.py        get / set / edit / show
    doctor.py        installation diagnostics
    killall.py       emergency process kill
    llm.py           llm-tiny stats / tail
    gh.py            gh status / issues / mirror / pr open/merge/reject
    release.py       start / status / list / publish / abort
    round.py         status / next / history / get / enqueue / advance / promote
    scaffold.py      extract / update / diff
    tool.py          invoke a single MCP tool / list all tools
```

## Adding a CLI subcommand

1. Create `commands/<name>.py` with a `handle(args) -> int` function that returns 0 on success, 1 on failure.
2. In `main.py`: add `_add_<name>(sub)` that builds the argparse parser, sets `args._handler = <name>.handle`, and calls it from `build_parser()`.
3. Import the handler in `main.py`'s top-level import from `commands`.

## Command handler pattern

All handlers follow the same shape:

```python
def handle(args) -> int:
    # 1. Resolve company_dir from CWD if .company/ exists
    cd = _company_dir(args)  # or from args.project_dir

    # 2. Build payload dict for MCP tool call
    payload = {"key": args.value}
    if cd: payload["company_dir"] = cd

    # 3. Call MCP tool
    res = mcp_client.call_tool("tool_name", payload)

    # 4. Print JSON result, return exit code
    print(json.dumps(res, indent=2))
    return 0 if res.get("ok") else 1
```

Some handlers (like `init.py` and `company.py`) bypass the MCP client and shell out to bash scripts directly via `subprocess`.

## mcp_client.py

- `_http_session()` / `_stdio_session()`: async context managers for the two transports.
- `_call_async(name, arguments)`: picks HTTP if the server is up, else falls back to stdio (one-shot).
- `call_tool(name, arguments)`: sync wrapper around `_call_async` using `asyncio.run()`.
- `list_tools()`: lists registered MCP tool names. Falls back to introspecting the server module if the server isn't running.

Key env vars: `HIVE_MCP_HOST`, `HIVE_MCP_PORT`, `HIVE_ROOT`, `HIVE_INSTALL_DIR`.

## Path resolution

- `HIVE_INSTALL_DIR`: global install root (venv + source). Resolved from `HIVE_INSTALL_DIR` env, or `parents[2]` from this file's location.
- `HIVE_ROOT`: per-project scaffold root. Set by `bin/hive` launcher. Falls back to `HIVE_INSTALL_DIR` for dev runs.

## Running without install

```bash
PYTHONPATH=lib python -m hive_cli.main --help
PYTHONPATH=lib python -m hive_cli.main doctor
```

No venv required — `mcp_client` falls back to importing the server module directly when the HTTP server isn't reachable.

## See also

- Parent `CLAUDE.md` — Framework-level architecture, common commands
- `lib/CLAUDE.md` — Cross-module data flow
- `lib/hive_cli/commands/CLAUDE.md` — Per-subcommand handler patterns
