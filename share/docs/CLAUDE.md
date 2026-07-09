# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working within the `share/docs/` directory.

## Purpose

This directory is Hive's **documentation corpus** — the human-readable reference that complements the root `CLAUDE.md` and the many nested `CLAUDE.md` files in code directories. These files are also shipped with Hive to project users and appear in the extracted archive.

## File inventory

| File | Role | Audience |
|---|---|---|
| `README.md` | User-facing overview — install, quick start, architecture diagram, quality gates | External users, new team members |
| `ARCHITECTURE.md` | Deep dive into components, state model, quality gating pipeline, process model, language rationale | Developers extending Hive |
| `CLI.md` | Command reference for every `hive` subcommand with argument signatures | Operators, users |
| `MCP-TOOLS.md` | Catalog of ~49 MCP tools with argument lists | Agent prompt authors, MCP tool developers |
| `PACKAGING.md` | How to build a release tarball, install on targets, uninstall, host artifacts | Release engineers, packagers |

## How the docs relate

```
README.md          → entry point, links to the other three
ARCHITECTURE.md    → expands the "one-line tour" from README with process model and rationale
CLI.md             → exhaustive command reference (not in root CLAUDE.md)
MCP-TOOLS.md       → full tool catalog with args (not in root CLAUDE.md)
PACKAGING.md       → standalone release/distribution guide
```

The root `CLAUDE.md` covers the high-level architecture and common commands but **does not** include the exhaustive CLI reference, the full MCP tool catalog, or packaging details — those live in their respective files here.

## Cross-references to code CLAUDE.md files

When working on documentation that references specific code areas, the following nested CLAUDE.md files contain deeper detail:

- `lib/core/CLAUDE.md` — the 5 foundational bash scripts (claude-spawn, orchestrator, init, hooks)
- `lib/tools/CLAUDE.md` — 101 bash scripts organized by category (lifecycle, tickets, chat, gates, etc.)
- `lib/hive_mcp/tools/CLAUDE.md` — MCP tool modules, their domains, and the Python→bash bridge pattern
- `lib/agents/CLAUDE.md` — agent prompt architecture, role hierarchy, and prompt conventions
- `lib/dashboard/CLAUDE.md` — single-file Node.js SPA architecture
- `lib/hive_cli/CLAUDE.md` and `lib/hive_cli/commands/CLAUDE.md` — CLI dispatcher and handler patterns
- `packaging/CLAUDE.md` — installer behavior, hook registration, idempotency guarantees

## Maintenance notes

- The `README.md` architecture diagram uses ASCII art — keep it aligned with the actual component layout in `lib/`.
- `MCP-TOOLS.md` lists 49 tools — keep it in sync with `lib/hive_mcp/server.py`'s `TOOL_MODULES` list.
- `CLI.md` command signatures should match `lib/hive_cli/main.py`'s argparse definitions.
- All five docs are referenced from the root `CLAUDE.md` (README, ARCHITECTURE, CLI, MCP-TOOLS) and from `share/docs/README.md` itself.

## See also

- Parent `CLAUDE.md` — Framework-level architecture overview
- `lib/CLAUDE.md` — Cross-module map (reference)
