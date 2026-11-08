# Packaging & Distribution

Hive ships as a single `.tar.gz` archive (a few hundred KB). The
tarball carries its own bill of materials: `MANIFEST.txt` at the
package root is the exact, sorted entry list — the build writes it
into the staged package before tarring, and gate 4 of the cut verifies
it from the tarball (required runtime entries present, runtime-state
entries absent). Targets need only `bash`, `python3` (3.10+), `git`,
`jq`, and `node` (for the dashboard).

## Build a release tarball

```bash
bash ~/.local/share/hive/packaging/build-package.sh
# → dist/hive-<VERSION>.tar.gz
# → dist/hive-<VERSION>.sha256
```

The version is read from `VERSION` in the source root. Override with `--version`.
Output dir overridable with `--out-dir`.

The build is fully deterministic: sorted file list, owner/group zeroed, and
file mtimes pinned to a **stable per-release anchor** resolved in this order
(SOURCE_DATE_EPOCH convention):

1. `$SOURCE_DATE_EPOCH`, if set — pin it explicitly for a release.
2. The last git commit that touched `VERSION` — stable across rebuilds of the
   same tree. Cutting a release edits (and commits) `VERSION`; rebuilding the
   same tag reproduces the bytes exactly. `touch VERSION` does not change the
   hash.
3. The HEAD commit date (shallow clones where `VERSION`'s history is absent).
4. Fixed epoch 0 for trees outside git.

## Cutting a hive release — the only path

A release is cut in exactly one way, and gate 0 of the cut script
refuses any tree where the version-bearing surfaces disagree:

1. **The cut commit — one commit.** Bump `VERSION`,
   `packaging/pyproject.toml` (`version`), the newest `CHANGELOG.md`
   section, and both READMEs (`README.md` and `share/docs/README.md`:
   the "Latest release" line and the tarball-name lines) to the new
   version. Validate the commit before the cut:

   ```bash
   bash packaging/cut-release.sh --check-surface
   ```

2. **The cut.** `bash packaging/cut-release.sh` — run `--dry-run` first
   if you want every gate without the tag, push, or publish. Gates, in
   order: 0 every version-bearing surface agrees (a mismatch fails,
   naming each stale file), 1 git state (clean tree, no existing tag),
   2 the test suite green, 3 build-package, 4 the shipping manifest
   (MANIFEST.txt extracted from the tarball — required runtime entries
   present, runtime-state entries absent, each offender named), 5
   credential scan, 6 sha256 sidecar, 7 release notes — then
   `git tag v<version>`, push, and `gh release create` with the
   tarball + sha256 sidecar.

Hand-taged cuts (tagging and publishing by hand, outside the script)
are **not a path**. The 0.10.0 cut was done that way and left both
READMEs one release behind until caught post-push; gate 0 now covers
every version-bearing surface, so a cut cannot leave one stale.

### Committing the checksum sidecar with a release tag

The `release.yml` workflow (installed by `gh-init.sh`) re-builds the tarball
on a clean runner when a `vX.Y.Z` tag is pushed and verifies the rebuild
**against the `dist/hive-X.Y.Z.sha256` sidecar committed with the tag** — the
original published checksum, not the sidecar the rebuild just generated (that
would be a self-referential check that can never detect drift). So when
cutting a release:

```bash
bash packaging/build-package.sh
git add dist/hive-X.Y.Z.sha256   # the sidecar must be in the tag commit
git commit -m "release: X.Y.Z"
```

If the sidecar is missing the workflow fails loudly instead of shipping an
unverified rebuild; if the rebuilt tarball's sha256 differs from the committed
one, the upload is refused.

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
