#!/usr/bin/env bash
# entries.sh — the shared entry-set parser (S2, 0.12).
#
# The shipped top-level entry set is defined ONCE, in
# packaging/entries.list, and shared by every entry point that stages
# a Hive copy: packaging/build-package.sh (the tarball + MANIFEST.txt)
# and packaging/install.sh --copy-to (the install copy). This file
# carries the ONE parser for the list format, so the format cannot
# drift between the two consumers, and neither consumer keeps a second
# hand-maintained staging list.
#
# Usage (bash — both consumers are bash):
#   . "$(cd "$(dirname "$0")" && pwd)/entries.sh"
#   while read -r SRC DEST; do ... done < <(entry_pairs "$LIST")

# entry_pairs <entries.list>
# Emit "SRC DEST" pairs, one per non-comment line of the list. A
# one-field line is an entry at the source root (dir or file, SRC ==
# DEST); "SRC as DEST" stages SRC under the name DEST (the public
# README.md ships from share/docs/README.md). Malformed lines are
# ignored by the awk program — the per-entry existence checks in the
# consumers are the real validation.
entry_pairs() {
  awk '
    { sub(/#.*/, "") }
    /^[[:space:]]*$/ { next }
    NF == 1 { print $1, $1; next }
    NF == 3 && $2 == "as" { print $1, $3 }
  ' "$1"
}
