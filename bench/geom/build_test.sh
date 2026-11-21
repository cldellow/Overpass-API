#!/bin/bash
# Build geom_props against one Overpass source tree and its build directory.
#
#   build_test.sh SRC BUILD OUT [OBJ=REPLACEMENT.o ...]
#
# SRC is the source checkout (or its src/ directory), BUILD a configured build directory of
# it in which `make bin/osm3s_query` (or a full build) has run. The test links the objects
# osm3s_query is linked from, minus its main(), so it runs that build's code. Each
# OBJ=REPLACEMENT.o swaps one of those objects (path relative to BUILD) for another; mutate.sh
# uses it.
#
# The source is probed for the functions each property needs; the results are passed as
# GEOM_HAVE_* macros and written to OUT.features.

set -euo pipefail

if [ $# -lt 3 ]; then
  echo "usage: $0 SRC BUILD OUT [OBJ=REPLACEMENT.o ...]" >&2
  exit 2
fi
SRC=$(cd "$1" && pwd)
[ -d "$SRC/src/overpass_api" ] && SRC="$SRC/src"
BUILD=$(cd "$2" && pwd)
OUT=$3
shift 3
HERE=$(cd "$(dirname "$0")" && pwd)

if [ ! -f "$SRC/overpass_api/statements/area_query.h" ]; then
  echo "$SRC: not an Overpass source tree" >&2
  exit 2
fi
if [ ! -f "$BUILD/Makefile" ] || [ ! -f "$BUILD/libcore.la" ]; then
  echo "$BUILD: not a built Overpass build directory (run make bin/osm3s_query there)" >&2
  exit 2
fi

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

# Ask the build's own Makefile for the objects, libraries and flags of osm3s_query.
cat > "$TMP/print.mk" <<'EOF'
geom-print-objs:
	@echo $(bin_osm3s_query_OBJECTS)
geom-print-libs:
	@echo $(bin_osm3s_query_LDADD) $(LIBS)
geom-print-cxx:
	@echo $(CXX) $(DEFS) $(CPPFLAGS) $(CXXFLAGS)
EOF
mk() { make -s --no-print-directory -C "$BUILD" -f Makefile -f "$TMP/print.mk" "$1"; }
OBJS=$(mk geom-print-objs)
LIBS=$(mk geom-print-libs)
CXX=$(mk geom-print-cxx)

# The objects to link: all of osm3s_query's except the one holding main(), with replacements.
declare -A REPLACE=()
for r in "$@"; do
  REPLACE[${r%%=*}]=${r#*=}
done
LINK_OBJS=()
for o in $OBJS; do
  case "$o" in
    */osm3s_query.o) continue ;;
  esac
  if [ -n "${REPLACE[$o]:-}" ]; then
    LINK_OBJS+=("${REPLACE[$o]}")
  else
    [ -f "$BUILD/$o" ] || { echo "$BUILD/$o missing: rebuild bin/osm3s_query" >&2; exit 2; }
    LINK_OBJS+=("$BUILD/$o")
  fi
done

# Probe the source for each function the properties use.
probe() {
  local name=$1 body=$2
  cat > "$TMP/probe_$name.cc" <<EOF
#include "overpass_api/statements/area_query.h"
#include "overpass_api/statements/coord_query.h"
void geom_probe() { $body }
EOF
  if $CXX -I"$BUILD" -I"$SRC" -fsyntax-only "$TMP/probe_$name.cc" 2>/dev/null; then
    echo "$name"
  fi
}
FEATURES=$(
  probe DECODED_COORDS 'Decoded_Coords c; Area_Block b; decode_coords(b, c); int r = intersects_inner(c, c); (void)r;' &
  probe BLOCK_INTERSECTS 'Area_Block b; int r = intersects_inner(b, b); (void)r;' &
  probe COORDS_BOUNDS 'Decoded_Coords c; Coords_Bounds x(c); bool o = x.overlaps(x); (void)o;' &
  probe DECODED_AREA_BLOCK 'Area_Block b; Coord_Query_Statement::Decoded_Area_Block d(0, b);
      int r = Coord_Query_Statement::check_area_block(d, 0u, 0); int32 m = d.min_lon + d.max_lon; (void)r; (void)m;' &
  probe DECODED_HAS_INNER_POINTS 'Decoded_Coords c; Area_Block b; Coord_Query_Statement::Decoded_Area_Block d(0, b);
      int i = 0; has_inner_points(c, d, i);' &
  probe BLOCK_HAS_INNER_POINTS 'Area_Block b; int i = 0; has_inner_points(b, b, i);' &
  wait
)
DEFINES=()
for f in $FEATURES; do
  DEFINES+=("-DGEOM_HAVE_$f")
done
echo "$FEATURES" | sort | tr '\n' ' ' > "$OUT.features"

$CXX -I"$BUILD" -I"$SRC" "${DEFINES[@]}" -c "$HERE/geom_props.cc" -o "$TMP/geom_props.o"
# libtool resolves the build's convenience libraries (libcore.la, libdata.la).
"$BUILD/libtool" --quiet --tag=CXX --mode=link $CXX -o "$OUT" "$TMP/geom_props.o" "${LINK_OBJS[@]}" \
    $(for l in $LIBS; do case "$l" in *.la) echo "$BUILD/$l" ;; *) echo "$l" ;; esac; done)
