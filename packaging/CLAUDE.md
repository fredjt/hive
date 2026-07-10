# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this directory.

## What is here

This directory contains the packaging artifacts: installers, the build script, and Python project metadata.

## File inventory

| File | Purpose |
|---|---|
| `install.sh` | Main installer — creates Python venv (uv or stdlib), installs deps, chmods scripts, symlinks `hive` onto PATH, registers PreToolUse hooks in `~/.claude/settings.json` |
| `install-online.sh` | Remote one-liner installer (`curl | sh`) — downloads a tarball, verifies checksum, delegates to `install.sh` |
| `build-package.sh` | Produces a redistributable `dist/hive-<version>.tar.gz` + `.sha256` from the source tree |
| `uninstall.sh` | Removes the `hive` symlink and venv; leaves source tree and `.company/` workspaces untouched |
| `pyproject.toml` | Python project metadata (setuptools) — defines `hive-os` package, entry point `hive → hive_cli.main:main` |
| `requirements.txt` | Pinned minimum Python dependencies: mcp, fastapi, uvicorn, httpx, tomli |

## Key behaviors

### install.sh

- **Idempotent**: re-running upgrades the venv and re-creates the symlink without touching project workspaces.
- **Two modes**: installs in-place by default; pass `--copy-to <dir>` to copy the tree first (the typical production flow).
- **Venv creation**: prefers `uv venv --seed`, falls back to `python3 -m venv`.
- **Hook registration**: uses `jq` to patch `~/.claude/settings.json` with two PreToolUse hooks (`block-company-writes.sh`, `block-admin-code-writes.sh`) under matcher `Write|Edit|NotebookEdit|Bash`. Includes migration logic to replace old narrow-match registrations.
- **Daemon detection**: after install, prints a warning if the MCP server or sprint-lifecycle are still running with old code, suggesting `hive stop && hive start`.

### build-package.sh

- Produces a deterministic tarball: sorted file list, fixed mtimes, zeroed owner/group.
- Stages a clean copy in a temp dir, copies `bin lib etc share packaging VERSION`, adds README/LICENSE/INSTALL, then prunes `__pycache__`, `.git`, `venv`, `dist`, `.company`, `.worktrees`, `node_modules`, `.pyc`, `.DS_Store`, `.swp`.
- Reads version from the top-level `VERSION` file; override with `--version X.Y.Z`.
- Output goes to `dist/hive-<version>.tar.gz` + `dist/hive-<version>.sha256`.

### install-online.sh

- Pure POSIX sh (no bashisms) for minimal container compatibility.
- Accepts `HIVE_PKG_URL` (remote tarball URL) or `HIVE_PKG_FILE` (local path).
- Verifies SHA-256 checksum from a `.sha256` sidecar file if available.
- Extracts and delegates to `packaging/install.sh --copy-to`.

## Dependencies

```
mcp>=1.0
fastapi>=0.110
uvicorn[standard]>=0.27
httpx>=0.27
tomli>=2.0; python_version < "3.11"
```

Python runtime requires **>=3.10** (per `pyproject.toml`).

## Common commands

```bash
# Build a distributable tarball from source
bash packaging/build-package.sh [--version X.Y.Z] [--out-dir DIR]

# Install from source tree (in-place)
bash packaging/install.sh

# Install to a custom directory (typical production flow)
bash packaging/install.sh --copy-to ~/.local/share/hive

# One-liner remote install
curl -fsSL <url-to-install-online.sh> | HIVE_PKG_URL=<tarball-url> sh

# Uninstall
bash packaging/uninstall.sh
```

## See also

- `share/docs/PACKAGING.md` — Release/distribution guide
- Parent `CLAUDE.md` — Framework-level architecture, common commands
