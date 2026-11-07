#!/usr/bin/env bash
set -uo pipefail

# release-publish.sh — release-ic's GO / NO-GO commit.
#
# Usage:
#   release-publish.sh GO    vX.Y.Z "<rationale>"
#   release-publish.sh NO-GO vX.Y.Z "<rationale>"
#
# GO    → `gh release create vX.Y.Z dist/hive-X.Y.Z.tar.gz dist/hive-X.Y.Z.sha256
#                          --title 'hive vX.Y.Z' --notes "<rationale + changelog>"`
#         then mark phase=done, release_url=…, adversarial_verdict=GO
# NO-GO → mark phase=failed, adversarial_verdict=NO-GO, file an IT ticket.

VERDICT="${1:-}"
VERSION="${2:-}"
shift 2 2>/dev/null || true
RATIONALE="$*"

[ -z "$VERDICT" ] || [ -z "$VERSION" ] || [ -z "$RATIONALE" ] && {
  echo "Usage: release-publish.sh GO|NO-GO vX.Y.Z '<rationale>'" >&2
  exit 2
}

PROJECT_ROOT="${PROJECT_ROOT:-$PWD}"
SKILL_DIR="${HIVE_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}"
# shellcheck source=/dev/null
. "$SKILL_DIR/lib/tools/hive-state.sh"
COMPANY_DIR="$PROJECT_ROOT/.company"
export COMPANY_DIR
. "$SKILL_DIR/lib/tools/gh-helpers.sh"

case "$VERSION" in v*) ;; *) VERSION="v$VERSION" ;; esac
ver_no_v="${VERSION#v}"
rec="$COMPANY_DIR/releases/${VERSION}.json"
[ -f "$rec" ] || { echo "release-publish: no record at $rec" >&2; exit 1; }

now=$(date -u +%Y-%m-%dT%H:%M:%SZ)

case "$VERDICT" in
  GO)
    cd "$PROJECT_ROOT"
    tarball="$SKILL_DIR/dist/hive-${ver_no_v}.tar.gz"
    sha_file="$SKILL_DIR/dist/hive-${ver_no_v}.sha256"
    [ -f "$tarball" ] || { echo "release-publish: missing $tarball" >&2; exit 1; }
    [ -f "$sha_file" ] || { echo "release-publish: missing $sha_file" >&2; exit 1; }

    # Find a CHANGELOG section if any (notes are shared by both publish
    # paths — the changelog entry is headed '## X.Y.Z' without the v,
    # so match on the version without it)
    notes="$RATIONALE"
    for c in CHANGELOG.md CHANGELOG marketing/output/CHANGELOG.md .company/marketing/output/CHANGELOG.md; do
      if [ -f "$c" ] && grep -qF "$ver_no_v" "$c"; then
        # Extract from the version heading down to the next ##/--- (best-effort)
        excerpt=$(awk -v ver="$ver_no_v" '
          $0 ~ ver { capture=1; print; next }
          capture && /^## / { exit }
          capture { print }' "$c" | head -120)
        [ -n "$excerpt" ] && notes="$RATIONALE

---

$excerpt"
        break
      fi
    done

    NOTES_FILE=$(mktemp -t hive-publish-notes.XXXXXX.md)
    trap 'rm -f "$NOTES_FILE"' EXIT
    printf '%s\n' "$notes" > "$NOTES_FILE"

    if gh_preflight 2>/tmp/release-publish.preflight; then
      # Idempotent: if the release already exists, just upload (clobber)
      if gh release view "$VERSION" >/dev/null 2>&1; then
        gh release upload "$VERSION" "$tarball" "$sha_file" --clobber 2>&1 \
          || { echo "release-publish: upload failed" >&2; exit 1; }
        url=$(gh release view "$VERSION" --json url -q .url 2>/dev/null)
      else
        url=$(gh release create "$VERSION" "$tarball" "$sha_file" \
              --title "hive $VERSION" --notes "$notes" 2>&1 | grep -oE 'https://github.com/[^[:space:]]+' | sed -n 1p)
      fi
    else
      cat /tmp/release-publish.preflight >&2
      echo "release-publish: gh unavailable — publishing via the local-only publisher"
      if ! bash "$SKILL_DIR/lib/templates/publishers/local-only.sh" \
           "$VERSION" "$tarball" "$sha_file" "$NOTES_FILE"; then
        echo "release-publish: local-only publish failed" >&2
        exit 1
      fi
      url="file://$PROJECT_ROOT/dist/releases/$VERSION"
      RATIONALE="$RATIONALE (local-only: gh unavailable)"
    fi

    hs jq "releases/${VERSION}.json" \
      --arg url "$url" --arg rat "$RATIONALE" --arg now "$now" \
      '.phase = "done" | .adversarial_verdict = "GO" | .release_url = $url
       | .go_rationale = $rat | .ended_at = $now' >/dev/null || exit 1
    echo "release-publish: $VERSION published → $url"
    ;;
  NO-GO)
    hs jq "releases/${VERSION}.json" \
      --arg rat "$RATIONALE" --arg now "$now" \
      '.phase = "failed" | .adversarial_verdict = "NO-GO" | .nogo_rationale = $rat
       | .ended_at = $now | .blockers = ((.blockers // []) + [$rat])' >/dev/null || exit 1
    bash "$SKILL_DIR/lib/tools/ticket-create.sh" it bug high \
      "Release $VERSION BLOCKED" "release-ic NO-GO. Rationale: $RATIONALE" \
      "release-ic" "release-blocked" >/dev/null || true
    bash "$SKILL_DIR/lib/tools/chat-send.sh" leadership "release-ic" "executive" \
      "❌ Release $VERSION blocked by adversarial verification: $(echo "$RATIONALE" | head -c 200)" >/dev/null 2>&1 || true
    echo "release-publish: $VERSION marked NO-GO; IT ticket filed."
    ;;
  *)
    echo "release-publish: VERDICT must be GO or NO-GO" >&2
    exit 2
    ;;
esac
