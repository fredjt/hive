# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is here

Scaffold templates auto-installed by `lib/tools/gh-init.sh` into projects as part of the `.hive/` directory. These files become part of a Hive-initialized project's GitHub integration and release pipeline.

## Directory layout

```
templates/
├── PULL_REQUEST_TEMPLATE.md          → <project>/.github/PULL_REQUEST_TEMPLATE.md
├── github-workflows/
│   ├── ci.yml                        → <project>/.github/workflows/ci.yml
│   └── release.yml                   → <project>/.github/workflows/release.yml
└── publishers/
    ├── github.sh                     → <project>/.hive/publishers/github.sh (default)
    ├── local-only.sh                 → <project>/.hive/publishers/local-only.sh
    └── CLAUDE.md                     (publisher docs)
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

Changes affect **all future project initializations**. Existing projects have their own copy and won't be updated automatically.

- **GitHub workflows** (`github-workflows/CLAUDE.md`): The CI workflow's language detection and gate list should stay in sync with `lib/tools/verification-gates.sh`. The release workflow assumes `packaging/build-package.sh` and a `VERSION` file exist.
- **Publishers** (`publishers/CLAUDE.md`): Custom publishers follow the 4-arg signature.

## See also

- `github-workflows/CLAUDE.md` — CI and release workflow details
- `publishers/CLAUDE.md` — Release publisher scripts
- Parent `CLAUDE.md` — Framework-level architecture, common commands
