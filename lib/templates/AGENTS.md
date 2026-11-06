# AGENTS.md

This file documents the `this directory` area of the Hive project — what it does, how it works, and the conventions for extending it.

## What is here

Templates auto-installed by `lib/tools/gh-init.sh` into a project's `.github/` directory (workflows + PR template) as part of `hive init`. The release publishers are the exception: they stay in this install's tree and run from there (see `publishers/AGENTS.md`) — nothing is ever copied into a per-project scaffold (there is no such thing).

## Directory layout

```
templates/
├── PULL_REQUEST_TEMPLATE.md          → <project>/.github/PULL_REQUEST_TEMPLATE.md
├── github-workflows/
│   ├── ci.yml                        → <project>/.github/workflows/ci.yml
│   └── release.yml                   → <project>/.github/workflows/release.yml
└── publishers/
    ├── github.sh                     (default publisher — runs from the install,
    │                                  $HIVE_ROOT/lib/templates/publishers/github.sh)
    ├── local-only.sh                 (runs from the install; operator-selectable
    │                                  via [release].publisher_script)
    └── AGENTS.md                     (publisher docs)
```

## The three template families

### 1. Pull Request template

Hive-specific PR body with sections for ticket linkage, TDD evidence checklist, and verification criteria. Auto-filled with the hive ticket ID when PRs are opened via `gh-pr-open.sh`.

### 2. GitHub Actions workflows

Mirrors Hive's local verification gates (`verification-gates.sh`) on GitHub's cloud runners:

- **ci.yml** — Fires on PRs and pushes to `main`. Classifies whether a PR touches source files (skips code gates for doc-only PRs), then runs TDD-order check, tests, lint, and security audit across Node/Python/Rust/Go.
- **release.yml** — Fires on `v*` tag pushes. Verifies VERSION file matches tag, re-builds the tarball deterministically, validates sha256, and uploads artifacts to the GitHub Release.

### 3. Release publishers

Bash scripts invoked by `release-publish-runner.sh` (stage 8 of the sprint cycle) with four positional args: `version`, `tarball_path`, `sha256_path`, `changelog_path`. The default (`github.sh`) creates an annotated tag and publishes via `gh release create`. `local-only.sh` copies artifacts to `dist/releases/<version>/`.

## Modifying templates

Copied families (workflows, PR template) affect **all future project initializations**; existing projects keep the copy that was installed into their `.github/` and won't be updated automatically. The publisher family is never copied — it always runs from the install, so a publisher change affects every project immediately.

- **GitHub workflows** (`github-workflows/AGENTS.md`): The CI workflow's language detection and gate list should stay in sync with `lib/tools/verification-gates.sh`. The release workflow assumes `packaging/build-package.sh` and a `VERSION` file exist.
- **Publishers** (`publishers/AGENTS.md`): Custom publishers follow the 4-arg signature.

## See also

- `github-workflows/AGENTS.md` — CI and release workflow details
- `publishers/AGENTS.md` — Release publisher scripts
- Parent `AGENTS.md` — Framework-level architecture, common commands
