# Packaging & Distribution

Hive ships as a single `.tar.gz` archive (a few hundred KB). The
tarball carries its own bill of materials: `MANIFEST.txt` at the
package root is the exact, sorted entry list — the build writes it
into the staged package before tarring, and gate 4 of the cut verifies
it from the tarball (required runtime entries present, runtime-state
entries absent). Targets need only `bash`, `python3` (3.11+), `git`,
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

## The venv lock (T1, 0.14)

`packaging/requirements.txt` carries FLOOR pins only (`mcp>=1.0`, ...):
`pip install -r requirements.txt` resolves whatever PyPI serves on install
day, so an install today and an install next month can run different
versions of the same packages — and a release that verified green at cut
time could install broken months later, with no drift detectable anywhere
(the manifest covers the FILES, the receipt the digests, the doctor
installed-set check presence — nothing covered the venv's RESOLVED
versions).

`packaging/requirements.lock.txt` closes that hole: the Python environment
is a reproducible artifact — an exact pin per name in requirements.txt,
marker-aware (the `tomli` line is conditional on `python_version < "3.11"`).
One list, four consumers:

- **install.sh** installs FROM the lock when it ships with the release
  (pre-0.14 releases carry no lock and fall back to the floor pins with a
  note).
- **build-package.sh** self-verifies the lock before staging anything —
  the producer looks at its own output: (1) the lock exists, (2) every
  name in requirements.txt has a pin in the lock that satisfies the floor
  (a miss fails the build, naming the entry), (3) a fresh venv built from
  the lock imports the runtime set (the selftest probe; `HIVE_LOCK_VENV`
  lets the test suite point at a session-built venv instead of paying the
  network install per build).
- **hive doctor** cross-checks the installed venv against the installed
  lock (the "venv lock" check): a drifted package is named with the locked
  and the installed version; an install without a lock (pre-0.14) reports
  the resolved versions WITHOUT a FAIL.
- **the refresh path** — when requirements.txt changes:

  ```bash
  bash packaging/refresh-venv-lock.sh   # re-resolves into a throwaway venv
  git add packaging/requirements.lock.txt packaging/requirements.txt
  ```

  The refresh resolves the floors against what PyPI serves at refresh
time; a name whose marker does not apply to the refresh interpreter
  (e.g. `tomli` on 3.13) is resolved explicitly against its floor so the
  lock still pins it for the marker's audience.

The lock ships in the tarball (via the `packaging` entry, listed in
`MANIFEST.txt`) and is in the required-set constant, so gate 4, the
install's post-stage check, and doctor all require it in every install
from 0.14 on.

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
   (MANIFEST.txt extracted from the tarball — the required set and
   the state-free rule are the shared constant
   `packaging/required-entries.list`, each offender named), 5
   credential scan, 6 sha256 sidecar, 7 release notes — then
   `git tag v<version>`, push, and `gh release create` with the
   tarball + sha256 sidecar, then the S3 cut receipt
   (`hive-X.Y.Z.receipt.txt` — version, branch + commit, tag, gate
   0-7 results, asset + manifest sha256, release URL, timestamp)
   is written and uploaded as the third asset, `--clobber`-safe on
   re-run (the gate-2 line carries the suite's final summary line).

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
the tarball), extracts, and installs. With no env set it installs the latest
release of this repo from GitHub releases (VERSION is fetched from the raw
branch, so the URL is always current):

```bash
curl -fsSL https://raw.githubusercontent.com/fredjt/hive/main/packaging/install-online.sh | sh
```

Env vars (resolution order: `HIVE_PKG_URL` > `HIVE_PKG_FILE` > default):
- `HIVE_PKG_URL` — explicit tarball URL (wins over everything)
- `HIVE_PKG_FILE` — a local tarball (no download)
- `HIVE_REPO` — owner/repo for the default GitHub release URL
  (default `fredjt/hive`)
- `HIVE_BRANCH` — branch to fetch VERSION from (default `main`)
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
3. Installs the **venv lock** (`packaging/requirements.lock.txt` — exact
   pins, T1 0.14) into the venv; pre-lock releases fall back to
   `requirements.txt` (floor pins) with a note
4. `chmod +x` for `bin/hive`, `lib/core/*.sh`, `lib/tools/*.sh`
5. Symlinks `~/.local/bin/hive` → `<install>/bin/hive` (or wherever `--bin-link` points)
6. Prints PATH instructions if `~/.local/bin` isn't on PATH

It is **idempotent** — re-running upgrades the venv and refreshes the symlink.

After staging, install.sh verifies the installed set against the shared
required-set constant (`packaging/required-entries.list` — the same list
cut gate 4 uses, parsed by the one `required_entries()` helper in
`packaging/entries.sh`) and fails naming each missing entry. Since 0.13
the install also carries the tarball's `MANIFEST.txt`, which
`hive doctor` verifies at doctor time (the installed-set check): required
entries present, no runtime-state entry in the manifest or at the install
root — each offender named (a dev install without the generated manifest
gets a WARN, not a FAIL).

## What's NOT in the tarball

- `venv/` (recreated on the target)
- `dist/` (build artifacts)
- `__pycache__/`, `*.pyc` (Python build leftovers)
- `.git`, `.gitignore`
- `.company/`, `.worktrees/` (per-project test artifacts)
- `node_modules/` (dashboard has zero npm deps anyway)

## Uninstall (T3, 0.14 — teardown mirrors setup)

```bash
bash ~/.local/share/hive/packaging/uninstall.sh
```

Removes the venv and the `~/.local/bin/hive` symlink, and runs the
one-time legacy hook cleanup. What happens to the install tree
depends on who created it:

- **Staged install** (`install.sh --copy-to` — the dir carries the
  `.hive-install` marker the installer wrote): the uninstall removes
  the WHOLE install tree — no tree, no venv, no symlink. The scratch
  install is fully re-created by a fresh install.
- **In-place install** (a git checkout / the operator's own tree — no
  marker): the tree is the operator's work and is kept, as before —
  delete it manually if you want to fully remove Hive. The
  discriminator is the marker, not `.git`.

`uninstall.sh --keep-tree` is the escape hatch: on a marked install
it keeps the tree and removes only the symlink + venv (the pre-0.14
behavior). Project `.company/` directories are never touched.

## Scratch hygiene (T4, 0.14)

The pytest basetemp and the mktemp-shaped litter the suite leaves
under `$TMPDIR` used to snowball: every session's basetemp dir
(`pytest-of-<user>/pytest-<N>`, plus the xdist workers' dirs) and
every `tmp.*` / `hive-*` / `pip-unpack-*` scratch dir were kept
forever — the workspace compounded its own scratch instead of
reclaiming it. The retention policy, applied by construction:

- **the suite itself** — a session hook in `tests/conftest.py`
  (`_basetemp_retention`) keeps only the NEWEST 3 basetemp
generations under the basetemp root and reaps older ones (fresh-
mtime gated, so a live session — including the xdist workers that
each run the hook — is never touched).
- **the operator tool** — `packaging/clean-scratch.sh` applies the
  same policy to the mktemp litter: basetemp generations kept back
to the newest 3, and `tmp.*`/`hive-*`/`pip-unpack-*` direct children
of `$TMPDIR` reaped past a min age (3 days default, `--min-age`),
open-file gated (`lsof`, best-effort). `--dry-run` names the targets
without removing; it never touches fresh paths, and never touches
anything that is not a direct child of `$TMPDIR` in one of the
named scratch shapes — the workspace root itself is out of scope
by construction even when `$TMPDIR` sits inside it.

```bash
bash ~/.local/share/hive/packaging/clean-scratch.sh --dry-run   # see what would go
bash ~/.local/share/hive/packaging/clean-scratch.sh             # reap it
```

The snowball is bounded both ways: the reaper reaps what accumulated,
and `tests/test_xdist_contract.py::TestScratchSnowballGuard` pins the
module-level mktemp call sites in the suite, so a new per-test mktemp
call fails the guard until it is deliberately pinned.

## CI — the scheduled 3.13 full row (T2, 0.14)

The push matrix lives in `.github/workflows/ci.yml` (S4, 0.12: full on
3.12, version-sensitive subsets on 3.10/3.13 — unchanged by 0.14).
0.13 recorded ONE full-suite run on 3.13 by hand — a datum, not a row:
nothing re-ran the full suite on 3.13 after it, and every push
exercised only the 147-test subset. `.github/workflows/scheduled.yml`
makes the datum recurring: a `full-313` job running the FULL suite on
Python 3.13, triggered by

- **schedule** — weekly cron (`23 4 * * 1`, Mondays 04:23 UTC)
- **workflow_dispatch** — manual: `gh workflow run scheduled.yml`

**First dispatch run** (recorded per R3 — the figure lives in the
tree, not in prose): run [42566985025](https://github.com/fredjt/hive/actions/runs/42566985025),
2026-11-09, against commit `316a54f` (T2, on top of T1):
**1,346 passed, 3 skipped in 272.78s** (full suite, Python 3.13,
`-n auto`, GitHub ubuntu-latest). Skip list re-audited against the
T5 (0.13) skip-reason contract — every skip names its condition:
`test_python_surface_is_ruff_clean` (ruff not installed in the job),
`test_full_install_touches_no_settings` (opt-in full-install round-
trip, `HIVE_FULL_INSTALL_TEST=1`), `test_live_0_12_0_assets_verify`
(live 0.12.0 assets not present in the CI checkout's dist/).

## Verifying a published release (one command)

`packaging/verify-release.sh <tag>` fetches a release's three assets
(tarball, sha256 sidecar, cut receipt) and verifies the release against
its receipt:

```
bash packaging/verify-release.sh v0.12.0
```

Checks, each failure named: (1) the sidecar matches the downloaded
tarball; (2) the receipt's tarball digest matches the recomputed one;
(3) the receipt's manifest digest matches the tarball's `MANIFEST.txt`
member; (4) the receipt parses as `key: value` lines carrying the
required keys (receipt, version, tag, commit, gate-0..gate-7 all PASS,
both assets, manifest, release). Exit 0/1. This is the S3 receipt's
consumer — the audit trail 0.12 made machine-verifiable, verified in
one command (T4). It is a packaging script, not a CLI command; it
needs `gh` (for `gh release download`) and `sha256sum`/`tar`.
Releases without a receipt (pre-0.12.0) are unverifiable by design.

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
