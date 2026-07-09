# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is here

GitHub Actions workflow templates, auto-installed by `lib/tools/gh-init.sh` into projects as `.github/workflows/`. These mirror Hive's local verification gates and release pipeline on GitHub's cloud runners.

## Workflows

### `ci.yml` — CI gates

Fired on PRs and pushes to `main`. Jobs run on `ubuntu-latest`.

**Flow:**
1. **Classify PR** — checks if the diff touches source-language files (`js|ts|py|rs|go|...`). If no source files changed, all code gates are skipped (non-code PRs ship documents, not source).
2. **Setup runtimes** — Node 20 (if `package.json`), Python 3.x (if `pyproject.toml`/`requirements.txt`), Rust stable (if `Cargo.toml`).
3. **Install** — `npm ci`/`npm install`, `pip install -e .`, `pip install -r requirements.txt`, `rustup default stable`.
4. **TDD-order gate** (PR only) — the first commit on the branch must add/modify a test file. Mirrors the local `verification-gates.sh` TDD check.
5. **Tests** — runs `npm test`, `pytest`, `cargo test`, `go test` as applicable.
6. **Lint** — `npm run lint`, `ruff check`, `cargo clippy`, `go vet`.
7. **Security** — `npm audit`, `pip-audit`, `cargo audit` (all `continue-on-error: true`, advisory only).

**Key design:** The source-file classification step prevents non-code PRs (marketing, legal, HR docs, research deliverables) from failing language-specific gates.

### `release.yml` — release publishing

Fired on `push` of tags matching `v*`. Requires `contents: write` permission.

**Flow:**
1. **Verify VERSION** — checks that `VERSION` file matches the git tag.
2. **Re-build deterministically** — runs `packaging/build-package.sh` if it exists, to produce a clean tarball on a fresh runner.
3. **Verify sha256** — validates `dist/hive-<ver>.sha256` sidecar.
4. **Upload to GitHub Release** — uploads tarball + sha256 via `gh release upload --clobber`.

## Modifying templates

- These are **bash-based workflows** — no Node/Python build step. Edit directly.
- Changes here affect **all future project initializations**. Existing projects already have their own `.github/workflows/` copy and won't be updated automatically.
- The CI workflow's language detection and gate list should stay in sync with `lib/tools/verification-gates.sh` (the local counterpart).
- The release workflow assumes `packaging/build-package.sh` and a `VERSION` file exist in the project root.

## Runner

Both workflows target `ubuntu-latest`. No matrix builds or cross-platform testing.

## See also

- `../CLAUDE.md` — Template overview
- `publishers/CLAUDE.md` — Release publishers
- Parent `CLAUDE.md` — Framework-level architecture
