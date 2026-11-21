#!/bin/bash
# Run the geometry property tests against one Overpass build.
#
#   run.sh [--baseline BASE_SRC BASE_BUILD] SRC BUILD
#
# SRC is a source checkout (or its src/ directory), BUILD its build directory after
# `make bin/osm3s_query` (or a full build). Properties whose functions the source doesn't
# have are reported as SKIP.
#
# With --baseline, the exact geometry tests are also evaluated over the same grids in both
# builds and must agree case by case.
#
# Exit status: 0 if every check passed or was skipped, 1 if any failed, 2 on usage or
# build errors.

set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)

BASE_SRC=
BASE_BUILD=
if [ "${1:-}" = "--baseline" ]; then
  [ $# -ge 3 ] || { echo "usage: $0 [--baseline BASE_SRC BASE_BUILD] SRC BUILD" >&2; exit 2; }
  BASE_SRC=$2
  BASE_BUILD=$3
  shift 3
fi
if [ $# -ne 2 ]; then
  echo "usage: $0 [--baseline BASE_SRC BASE_BUILD] SRC BUILD" >&2
  exit 2
fi

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

"$HERE/build_test.sh" "$1" "$2" "$TMP/candidate" || exit 2
echo "Source probes: $(cat "$TMP/candidate.features")"
status=0
"$TMP/candidate" props || status=$?
[ $status -le 1 ] || exit 2

if [ -n "$BASE_SRC" ]; then
  "$HERE/build_test.sh" "$BASE_SRC" "$BASE_BUILD" "$TMP/baseline" || exit 2
  echo
  echo "Exact tests against the baseline ($(cat "$TMP/baseline.features")):"
  "$TMP/baseline" dump "$TMP/baseline.dump" > /dev/null
  "$TMP/candidate" dump "$TMP/candidate.dump" > /dev/null
  cmp_status=0
  "$TMP/candidate" compare "$TMP/baseline.dump" "$TMP/candidate.dump" || cmp_status=$?
  [ $cmp_status -le 1 ] || exit 2
  [ $cmp_status -eq 0 ] || status=1
fi

exit $status
