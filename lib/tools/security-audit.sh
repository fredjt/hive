#!/usr/bin/env bash
# Security-audit tool detection for a worktree.
#
# Single source of truth for gate 6 (security-clean) in
# verification-gates.sh, kept separate so detection + install hints are
# testable without running the full gate suite.
#
# A missing tool is NOT a pass: the gate records status=skip with a
# detail that names the stack and what to install, so "never scanned"
# never reads like "scanned clean" on the dashboard.
#
# Usage:
#   security-audit.sh cmd  <worktree>   audit command, or empty if no tool
#   security-audit.sh hint <worktree>   what to install to enable the audit
#   security-audit.sh run  <worktree>   run the audit (0 clean, 1 findings,
#                                       2 nothing to run)
set -uo pipefail

ACTION="${1:-cmd}"
WT="${2:-.}"
qwt=$(printf '%q' "$WT")

stack() {
  if [ -f "$WT/package.json" ]; then
    echo "node"
  elif [ -f "$WT/pyproject.toml" ] || [ -f "$WT/requirements.txt" ]; then
    echo "python"
  elif [ -f "$WT/Cargo.toml" ]; then
    echo "rust"
  elif [ -f "$WT/go.mod" ]; then
    echo "go"
  else
    echo "unknown"
  fi
}

case "$ACTION" in
  cmd)
    case "$(stack)" in
      node)
        echo "cd $qwt && npm audit --audit-level=high --silent"
        ;;
      python)
        if command -v bandit >/dev/null; then
          echo "cd $qwt && bandit -r -f txt -ll -ll . 2>/dev/null"
        elif command -v pip-audit >/dev/null; then
          echo "cd $qwt && pip-audit --disable-pip"
        fi
        ;;
      rust)
        command -v cargo-audit >/dev/null && echo "cd $qwt && cargo audit --quiet"
        ;;
      go)
        command -v govulncheck >/dev/null && echo "cd $qwt && govulncheck ./..."
        ;;
    esac
    ;;
  hint)
    case "$(stack)" in
      node)
        echo "none - npm audit ships with npm"
        ;;
      python)
        echo "pip install bandit  (or: pip install pip-audit)"
        ;;
      rust)
        echo "cargo install cargo-audit"
        ;;
      go)
        echo "go install golang.org/x/vuln/cmd/govulncheck@latest"
        ;;
      *)
        echo "none - no recognizable project manifest (package.json, pyproject.toml, requirements.txt, Cargo.toml, go.mod)"
        ;;
    esac
    ;;
  run)
    CMD=$(bash "$0" cmd "$WT")
    if [ -z "$CMD" ]; then
      echo "security-audit: no security tool available for this stack (hint: $(bash "$0" hint "$WT"))" >&2
      exit 2
    fi
    bash -c "$CMD"
    ;;
  *)
    echo "Usage: security-audit.sh <cmd|hint|run> <worktree>" >&2
    exit 1
    ;;
esac
