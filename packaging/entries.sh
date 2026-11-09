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

# required_entries <required-entries.list> <SECTION>
# Emit the entries of SECTION (REQUIRED or STATE) from the shared
# required-set constant (packaging/required-entries.list), one per
# line. Section words and comments are skipped. T3 (0.13): the single
# constant's bash parser — cut-release.sh gate 4 and install.sh's
# post-stage check read it through this helper (doctor.py has the
# python counterpart; tests pin both against the file).
required_entries() {
  awk -v want="$2" '
    { sub(/#.*/, "") }
    /^[[:space:]]*$/ { next }
    $0 == "REQUIRED" { in_sec = (want == "REQUIRED"); next }
    $0 == "STATE"    { in_sec = (want == "STATE"); next }
    in_sec && NF == 1 { print $1 }
  ' "$1"
}
