# Packaging & Distribution

Hive ships as a single `.tar.gz` archive (140 files, ~170 KB). Targets need
only `bash`, `python3` (3.10+), `git`, `jq`, and `node` (for the dashboard).

## Build a release tarball

```bash
bash ~/.local/share/hive/packaging/build-package.sh
# → dist/hive-<VERSION>.tar.gz
# → dist/hive-<VERSION>.sha256
```

The version is read from `VERSION` in the source root. Override with `--version`.
Output dir overridable with `--out-dir`.

The build is deterministic where possible (sorted file list, fixed mtimes) so
two builds from the same source produce byte-identical tarballs.

## Install on a target machine — three options

### Option A: extract + run install.sh (simple)

```bash
curl -L https://example.com/hive-0.1.0.tar.gz | tar xz
cd hive-0.1.0
bash packaging/install.sh --copy-to ~/.local/share/hive
hive doctor
```

### Option B: one-line installer

`packaging/install-online.sh` is a POSIX-sh installer that downloads the
tarball, verifies its checksum (if a sidecar `.sha256` is published next to
the tarball), extracts, and installs:

```bash
curl -fsSL https://example.com/install-hive.sh | HIVE_PKG_URL=https://example.com/hive-0.1.0.tar.gz sh
```

Env vars:
- `HIVE_PKG_URL` — tarball URL (or use `HIVE_PKG_FILE` for a local file)
- `HIVE_INSTALL_DIR` — install location (default `~/.local/share/hive`)

### Option C: clone the install tree directly

If you have the source tree (e.g. checked out from git):

```bash
git clone <repo> hive
bash hive/packaging/install.sh --copy-to ~/.local/share/hive
```

## What `install.sh` does

1. Optionally copies the tree to `--copy-to <dir>` (default: install in place)
2. Creates a Python venv at `<install>/venv` (uses `uv` if available, else `python3 -m venv`)
3. Installs `requirements.txt` into the venv
4. `chmod +x` for `bin/hive`, `lib/core/*.sh`, `lib/tools/*.sh`
5. Symlinks `~/.local/bin/hive` → `<install>/bin/hive` (or wherever `--bin-link` points)
6. Prints PATH instructions if `~/.local/bin` isn't on PATH

It is **idempotent** — re-running upgrades the venv and refreshes the symlink.

## What's NOT in the tarball

- `venv/` (recreated on the target)
- `dist/` (build artifacts)
- `__pycache__/`, `*.pyc` (Python build leftovers)
- `.git`, `.gitignore`
- `.company/`, `.worktrees/` (per-project test artifacts)
- `node_modules/` (dashboard has zero npm deps anyway)

## Uninstall

```bash
bash ~/.local/share/hive/packaging/uninstall.sh
```

Removes the venv and the `~/.local/bin/hive` symlink. Source tree and any
project `.company/` directories are left alone — delete them manually if you
want a full purge.

## Hosting the tarball

Anywhere that serves it as a binary file works:
- GitHub releases — recommended; gives you free version-tagged URLs
- An internal HTTP file server
- S3 or similar object storage
- A web server (`python3 -m http.server`)

If you publish the `.sha256` sidecar next to the tarball at
`hive-X.Y.Z.sha256` (same base name, same path), `install-online.sh` will
verify it automatically.

## Future packaging

`packaging/pyproject.toml` exists for an eventual `pip install hive-os` flow
but is not required for the tarball install path. If you want to publish to
PyPI later, run `python -m build` from the install root.
