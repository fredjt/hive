#!/usr/bin/env bash
# Deprecation shim. gh-pr-reject.sh has been renamed to gh-pr-close.sh and
# is now scripted-only (gated behind HIVE_PR_OP_AUTHORIZED). Verification
# IC's bounce path no longer touches PRs at all — bounce keeps the PR open.
# PR close is now triggered by ticket-move.sh as a side-effect of any →
# done with reason=rejected, and goes through gh-pr-close.sh.
echo "gh-pr-reject.sh is deprecated. Use ticket-move.sh ... done <reason> <author> rejected" >&2
echo "to reject a ticket; PR close is handled automatically." >&2
exit 4
