# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is here

Release publisher templates, auto-installed by `lib/tools/gh-init.sh` into projects as part of the `.hive/` scaffold. These are bash scripts that the release pipeline invokes to publish a completed release artifact.

## Runner

`lib/tools/release-publish-runner.sh` (stage 8 of the sprint cycle) resolves which publisher to invoke — defaulting to `github.sh`, overridable via `[release].publisher_script` in `~/.config/hive/config.toml` — and calls it with four positional arguments:

| Arg | Description |
|-----|-------------|
| `$1` | version, e.g. `0.4.0` |
| `$2` | path to the tarball, e.g. `dist/hive-0.4.0.tar.gz` |
| `$3` | path to the sha256 sidecar |
| `$4` | path to a `.md` file containing release notes |

The runner sets `release.publish_status` to `published` or `failed` based on the script's exit code.

## Built-in publishers

### `github.sh` (default)

Requires `gh` CLI authenticated and a `origin` git remote. Creates an annotated (optionally signed) tag, pushes it, then calls `gh release create` with the tarball, sha256 sidecar, and changelog against the `main` branch target.

### `local-only.sh`

Copies tarball, sha256, and release notes into `<project>/dist/releases/<version>/`. No remote interaction. Useful for air-gapped projects or internal artifact directories.

## Writing a custom publisher

1. Create a new executable bash script in this directory.
2. Accept the same 4-arg signature (`version`, `tarball_path`, `sha256_path`, `changelog_path`).
3. Use `set -uo pipefail` and exit 0 on success, non-zero on failure.
4. Reference it from `~/.config/hive/config.toml`:

```toml
[release]
publisher_script = "/absolute/path/to/my-publisher.sh"
```

The runner sets `HIVE_PR_OP_AUTHORIZED=1` when invoking the publisher, which bypasses the PreToolUse write-blocking hooks so the publisher can perform git pushes and API calls.

## See also

- `../CLAUDE.md` — Template overview
- `../github-workflows/CLAUDE.md` — Release workflow
- Parent `CLAUDE.md` — Framework-level architecture
