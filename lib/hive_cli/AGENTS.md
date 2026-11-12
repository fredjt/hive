# AGENTS.md

This file documents the `lib/hive_cli/` area of the Hive project — what it does, how it works, and the conventions for extending it.

## Role

`hive_cli` is the Python CLI dispatcher for the Hive framework. It parses argparse subcommands and routes them to handler modules, which in turn call the MCP server (via `mcp_client`) to execute tools. It is the human-facing interface — the dashboard and spawned agents use the MCP server directly.

## Structure

```
lib/hive_cli/
  main.py            argparse entry point — build_parser() + main()
  mcp_client.py      Async MCP client (HTTP + stdio transports) with sync wrapper
  __init__.py        (empty)
  commands/          One module per CLI subcommand
    company.py       start / stop / status (+ per-daemon liveness & supervisor dead-state from processes.json)
    ticket.py        create / get / list / move / claim / comment / approve
    init.py          workspace initialization (delegates to bash)
    dashboard.py     launches Node.js dashboard server
    chat.py          send / read messages
    agent.py         spawn-ceo / run-vp / verify
    gates.py         run / check / status
    backlog.py       prioritize
    inbox.py         file / process
    config.py        get / set / edit / show
    doctor.py        installation diagnostics + .company/ daemon health (dead daemons fail the run)
    killall.py       emergency process kill
    llm.py           LLM usage stats / tail + endpoint test (B1)
    gh.py            gh status / issues / mirror / pr open/merge/reject
    release.py       start / status / list / publish / abort
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

Transport ladder (0.20 T1 — the stopped company answers in-process; the
dead stdio process retired from the CLI):

- `_http_session()`: async context manager for the Streamable HTTP rung
  (the SDK client — only reached when `_sdk_available()` is True, so
  its imports are the process's first SDK imports).
- `_in_process_server()` / `_call_in_process(name, arguments)`: the
  middle rung — `build_server()` cached per process; the registry
  entry's `.fn` is called directly. The very function object FastMCP
  wraps, so the envelope is byte-identical to the transport by
  construction (one implementation, no transport, no second SDK
  import, no spawn).
- `_call_async(name, arguments)`: walks the ladder — HTTP if the server
  is up, in-process when it is down (SDK importable), the house error
  envelope (`"in-process fallback unavailable"`) when there is neither.
- `call_tool(name, arguments)`: sync wrapper around `_call_async` using
  `asyncio.run()`.
- `list_tools()`: the tool names — HTTP when the server is up, the
  in-process registry when it is down (one path, no spawn).

The mcp SDK import is LAZY (0.20 T1): importing this module does not
import the SDK; it is resolved once per process on the first tool call
or list, so non-tool commands (version, --help, stop, killall, config)
pay only the CLI import. The stdio SERVER transport
(`python -m hive_mcp.server --transport stdio`) stays in
`hive_mcp/server.py` for its programmatic consumers — only the CLI
stops being one.

Key env vars: `HIVE_MCP_HOST`, `HIVE_MCP_PORT`, `HIVE_ROOT`, `HIVE_INSTALL_DIR`.

## Path resolution

- `HIVE_INSTALL_DIR`: global install root (venv + source). Resolved from `HIVE_INSTALL_DIR` env, or `parents[2]` from this file's location.
- `HIVE_ROOT`: global install root — the SAME directory as `HIVE_INSTALL_DIR`. Set by the `bin/hive` launcher (`export HIVE_ROOT="$HIVE_INSTALL_DIR"`); projects use installed scripts directly. There is no per-project scaffold.

## Running without install

```bash
PYTHONPATH=lib python -m hive_cli.main --help
PYTHONPATH=lib python -m hive_cli.main doctor
```

No venv required — `mcp_client` runs the tool in-process (build_server +
the registry's .fn) when the HTTP server isn't reachable.

## See also

- Parent `AGENTS.md` — Framework-level architecture, common commands
- `lib/AGENTS.md` — Cross-module data flow
- `lib/hive_cli/commands/AGENTS.md` — Per-subcommand handler patterns
