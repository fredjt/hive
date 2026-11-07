# AGENTS.md

This file documents the `share/docs/` area of the Hive project — what it does, how it works, and the conventions for extending it.

## Purpose

This directory is Hive's **documentation corpus** — the human-readable reference that complements the root `AGENTS.md` and the many nested `AGENTS.md` files in code directories. These files are also shipped with Hive to project users and appear in the extracted archive.

## File inventory

| File | Role | Audience |
|---|---|---|
| `README.md` | User-facing overview — install, quick start, architecture diagram, quality gates | External users, new team members |
| `ARCHITECTURE.md` | Deep dive into components, state model, quality gating pipeline, process model, language rationale | Developers extending Hive |
| `CLI.md` | Command reference for every `hive` subcommand with argument signatures | Operators, users |
| `MCP-TOOLS.md` | Catalog of ~49 MCP tools with argument lists | Agent prompt authors, MCP tool developers |
| `LLM-PROVIDERS.md` | LLM provider config: the `[llm]` section, provider registry, wire formats, unsloth, key precedence | Operators setting up the agent runtime |
| `PACKAGING.md` | How to build a release tarball, install on targets, uninstall, host artifacts | Release engineers, packagers |

## How the docs relate

```
README.md          → entry point, links to the other three
ARCHITECTURE.md    → expands the "one-line tour" from README with process model and rationale
CLI.md             → exhaustive command reference (not in root AGENTS.md)
MCP-TOOLS.md       → full tool catalog with args (not in root AGENTS.md)
LLM-PROVIDERS.md   → [llm] config, provider registry, wire formats, unsloth
PACKAGING.md       → standalone release/distribution guide
```

The root `AGENTS.md` covers the high-level architecture and common commands but **does not** include the exhaustive CLI reference, the full MCP tool catalog, or packaging details — those live in their respective files here.

## Cross-references to code AGENTS.md files

When working on documentation that references specific code areas, the following nested AGENTS.md files contain deeper detail:

- `lib/core/AGENTS.md` — the 5 foundational bash scripts (agent-spawn, orchestrator, init, hooks)
- `lib/tools/AGENTS.md` — 101 bash scripts organized by category (lifecycle, tickets, chat, gates, etc.)
- `lib/hive_mcp/tools/AGENTS.md` — MCP tool modules, their domains, and the Python→bash bridge pattern
- `lib/agents/AGENTS.md` — agent prompt architecture, role hierarchy, and prompt conventions
- `lib/dashboard/AGENTS.md` — modular Node.js SPA architecture (G8 split)
- `lib/hive_cli/AGENTS.md` and `lib/hive_cli/commands/AGENTS.md` — CLI dispatcher and handler patterns
- `packaging/AGENTS.md` — installer behavior, hook registration, idempotency guarantees

## Maintenance notes

- The `README.md` architecture diagram uses ASCII art — keep it aligned with the actual component layout in `lib/`.
- `MCP-TOOLS.md` lists every registered tool. It is **generated, not hand-edited** — after adding/removing a tool in `lib/hive_mcp/tools/`, run `python3 packaging/gen-mcp-tools-doc.py`. A test (`tests/test_mcp_tools.py::TestMCPToolsDoc`) fails CI if the checked-in doc drifts from the live server.
- `CLI.md` command signatures should match `lib/hive_cli/main.py`'s argparse definitions.
- All five docs are referenced from the root `AGENTS.md` (README, ARCHITECTURE, CLI, MCP-TOOLS) and from `share/docs/README.md` itself.

## See also

- Parent `AGENTS.md` — Framework-level architecture overview
- `lib/AGENTS.md` — Cross-module map (reference)
