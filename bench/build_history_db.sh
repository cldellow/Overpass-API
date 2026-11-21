#!/bin/bash
# Build an Overpass database with history (attic data) from a regional snapshot plus
# planet replication diffs.
#
# Usage: build_history_db.sh SNAPSHOT.osm.pbf DIFF_DIR OUT_DIR OVERPASS_BIN_DIR [BBOX]
#
#   SNAPSHOT.osm.pbf  a regional extract, e.g. from Geofabrik
#   DIFF_DIR          planet replication diffs NNNN.osc.gz (day or hour), covering the time from
#                     the snapshot's timestamp onwards, named so that `sort -V` orders them
#   OUT_DIR           receives osc/ (the regional diffs) and db/ (the database)
#   BBOX              LEFT,BOTTOM,RIGHT,TOP to filter to; defaults to the snapshot's data bbox,
#                     which long boundary and coastline ways can make much larger than the region
#
# The planet diffs are filtered to the objects that are in the region before or after each
# diff, so each edit keeps its real timestamp, version and user. Edits at or before the
# snapshot's timestamp are dropped, since the snapshot already contains them.
set -euo pipefail
SNAP=$1 DIFFS=$2 OUT=$3 BIN=$4 BBOX=${5:-}
mkdir -p "$OUT/work" "$OUT/osc"

[ -n "$BBOX" ] || BBOX=$(osmium fileinfo -e -g data.bbox "$SNAP" | tr -d '()')
SNAP_TS=$(osmium fileinfo -g header.option.timestamp "$SNAP")
echo "snapshot $SNAP: timestamp $SNAP_TS, bbox $BBOX"

ids_of() { osmium cat -f opl "$1" | cut -d' ' -f1; }

# Every object seen in the region so far: the snapshot, plus each day's regional objects.
# Cumulative, so objects outside BBOX that are in the snapshot keep their later edits.
ids_of "$SNAP" | sort -u > "$OUT/work/ids.txt"
prev="$SNAP"
for diff in $(ls "$DIFFS"/*.osc.gz | sort -V); do
  seq=$(basename "$diff" .osc.gz)
  osmium apply-changes "$prev" "$diff" -o "$OUT/work/full.osm.pbf" -O
  osmium extract -b "$BBOX" -s complete_ways "$OUT/work/full.osm.pbf" -o "$OUT/work/snap-$seq.osm.pbf" -O
  { cat "$OUT/work/ids.txt"; ids_of "$OUT/work/snap-$seq.osm.pbf"; } | sort -u > "$OUT/work/ids.new"
  mv "$OUT/work/ids.new" "$OUT/work/ids.txt"
  # getid exits with 1 when some ids are not in the diff, which is the normal case here.
  osmium getid -i "$OUT/work/ids.txt" "$diff" -f osc -o "$OUT/work/region.osc" -O || [ $? -eq 1 ]
  # Keep only edits after the snapshot's timestamp.
  python3 -I - "$OUT/work/region.osc" "$OUT/osc/$seq.osc" "$SNAP_TS" <<'EOF'
import sys, xml.etree.ElementTree as ET
src, dst, cutoff = sys.argv[1:]
tree = ET.parse(src)
root = tree.getroot()
kept = dropped = 0
for action in list(root):
    for el in list(action):
        if el.get('timestamp', '') <= cutoff:
            action.remove(el); dropped += 1
        else:
            kept += 1
    if len(action) == 0:
        root.remove(action)
tree.write(dst, encoding='UTF-8', xml_declaration=True)
print(f'  {dst}: {kept} edits kept, {dropped} at or before the snapshot dropped')
EOF
  [ "$prev" != "$SNAP" ] && rm -f "$prev"
  prev="$OUT/work/snap-$seq.osm.pbf"
done
rm -f "$OUT/work/full.osm.pbf" "$OUT/work/region.osc" "$OUT/work/ids.txt"

echo "importing snapshot with --keep-attic"
rm -rf "$OUT/db" && mkdir -p "$OUT/db"
osmium cat "$SNAP" -f osm -o - | "$BIN/update_database" --db-dir="$OUT/db/" --keep-attic
echo "applying regional diffs with --keep-attic"
"$BIN/update_from_dir" --osc-dir="$OUT/osc" --db-dir="$OUT/db/" --keep-attic
echo "done: $OUT/db"
