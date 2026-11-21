# Benchmark tools

`compare.py` runs the same queries against two Overpass builds on one database and
reports the CPU time (user + system) of each `osm3s_query` run, plus whether the two
builds produce identical output.

```bash
python3 bench/compare.py --a BASELINE/install/bin --b CANDIDATE/install/bin \
    --db /path/to/db --reps 7 bench/queries/*.ql
```

- **Baseline caching:** by default the baseline's results are cached
  (`~/.cache/overpass-bench/baseline.json`), keyed by a hash of its `osm3s_query` binary, the
  database path and the query text. Later comparisons against the same baseline only time the
  candidate. Changing the binary, the database or a query invalidates that entry.
- **Close comparisons:** with `--no-cache`, runs alternate between the two builds after one untimed
  warm-up each, so background noise affects both alike. Use this when the difference is small.
  Cached runs time the builds separately.
- **Placeholders:** a query may contain `{{NAME}}` placeholders, filled from the JSON file given
  with `--vars`. They keep values that must not be committed, such as user ids from a licensed
  extract, out of the query files. `pick_vars.py --bin BIN --db DB --out VARS.json` chooses them
  from a database; keep that file with the database.
- Output equality ignores lines that name the build (generator/version).
- Exit status is 1 if any output differs.

`queries/` holds the sample queries; their bounding boxes are in Halifax, Nova Scotia, so they
expect a database built from a Nova Scotia extract. Two more sets need databases with extra data:

- `queries-areas/`: area queries (`area[...]`, `(area.a)`, `is_in`), for a database with the area
  rules run (`osm3s_query --rules < rules/areas.osm3s`).
- `queries-history/`: `[date:]`, `[diff:]`, `newer` and user filters, for a database with history
  (see below). `h5` and `h6` take `{{UID}}`, so they need `--vars`.

- `queries-tools/`: queries shaped like those that popular client tools generate (overpass turbo,
  JOSM, QGIS QuickOSM, osmnx, osmdata and MapComplete; templates read from each tool's source),
  filled in for Halifax. Run them on the area database: the area and `poly:` forms need it.
  `t08` and `t09` carry the Halifax boundary (relation 9092218, 1,041 points) as a `poly:` filter,
  as osmnx sends a place polygon (osmnx also buffers it by 500 m first; these don't).

Run each set with `--db` pointed at its database; the baseline cache keeps them apart.

## Differential fuzzing

`fuzz.py` generates random Overpass QL queries and runs each one against two builds, reporting
any query whose output or exit status differs. The queries follow the Overpass QL grammar
(https://github.com/b1tw153/overpass-ebnf) and favour paths that select objects by id: id filters,
recursion, member and parent filters, set operations and loops. Tags, ids, roles and coordinates
come from a vocabulary built from the same extract as the database, so most queries return data.

```bash
# Once per extract: build the vocabulary (needs osmium-tool).
python3 bench/fuzz_vocab.py nova-scotia.osm.pbf vocab.json

# Check the outputs are deterministic (same build twice), then compare two builds.
python3 bench/fuzz.py --a BASE/install/bin --b BASE/install/bin --db DB --vocab vocab.json --seed 1 --count 500
python3 bench/fuzz.py --a BASE/install/bin --b NEW/install/bin  --db DB --vocab vocab.json --seed 2 --count 5000

# Reprint one query.
python3 bench/fuzz.py --vocab vocab.json --seed 2 --show 1234
```

- **Reproducible:** query N of seed S depends only on S, N and the vocabulary file (the run prints
  the vocabulary's hash), not on `--jobs` or on which other queries ran.
- **Reusable:** `--a` and `--b` take any two builds, so the same seeds can be replayed against every
  change.
- **Import changes:** `--db-b` runs `--b` against a second database. With the same build for `--a`
  and `--b`, this compares two databases, e.g. one imported by the baseline and one by a changed
  `update_database`, or the same file imported with two `--flush-size` values.
- **Failures** are written to `--out-dir` as `<seed>-<index>/{query.ql,a.out,b.out}`, and the exit
  status is 1.
- **Optional statement families:** `--features areas,history,users,complete` adds area queries
  (`area`, `is_in`, `map_to_area`, `pivot`), history (`[date:]`, `[diff:]`, `[adiff:]`, `newer`,
  `changed`, `retro`, `timeline`), user filters and `complete` loops. Use them only on a database
  that supports them: areas need the area rules run (`osm3s_query --rules < rules/areas.osm3s`),
  and history and users need a database built with `--keep-attic` from diffs with metadata.
  `fuzz_vocab.py --osc-dir DIR` adds the users and edit times from such diffs to the vocabulary.
- **Errors count:** error messages on stderr (and in the output) are part of each outcome, and the
  summary lists the most common ones, so invalid generated queries are visible rather than silently
  matching.
- **Lines naming the build** (generator/version) are ignored. Runs that hit the timeout or the memory
  limit are counted as skipped, not as differences.
- **Finding slow or degenerate queries:** `--report FILE.csv` writes one row per query with its
  status, the CPU time on each build, the output size and the CPU time per KB of output. Sort it to
  find queries that time out, cost a lot for little output, or got slower on the build under test,
  then reprint them with `--show`.

## A database with history

**From a full-history extract** (`.osh.pbf`): import it directly. `--keep-attic` includes the
metadata, so don't also pass `--meta`: the later flag wins, and `--meta` would drop the history.

```bash
osmium cat region.osh.pbf -f osh -o - | BASELINE/install/bin/update_database --db-dir=$PWD/db/ --keep-attic
```

- **Use `-f osh`, not `-f osm`:** only the history format marks deleted versions with
  `visible="false"`. With `-f osm` they look like nodes without coordinates and ways or relations
  without members.
- **`upstream/master` imports history incorrectly.** It ignores `visible="false"` and stores
  wrong geometry for older versions. Import with a build of the `better-imports` branch, which
  collects the import fixes; query with the build under test.

**From a snapshot plus replication diffs:** `build_history_db.sh` builds a database with history (attic data) from a regional extract plus
planet replication diffs:

```bash
# Daily diffs covering the time since the extract's timestamp, named so that sort -V orders them.
bench/build_history_db.sh nova-scotia.osm.pbf diffs/ out/ BASELINE/install/bin "-67.3,43.3,-59.6,47.1"
python3 bench/fuzz_vocab.py nova-scotia.osm.pbf vocab.json --osc-dir out/osc
python3 bench/fuzz.py --a BASE/install/bin --b NEW/install/bin --db out/db --vocab vocab.json \
    --features history,users,complete --seed 12 --count 2000
```

- **How the diffs are filtered:** each planet diff is applied to the previous regional snapshot and
  cut to the bounding box. The real diff is then filtered to every object seen in the region so
  far, so edits keep their actual timestamps, versions and users, deletions included.
- **The overlap with the snapshot:** edits at or before the snapshot's timestamp are dropped, since
  the snapshot already contains them.
- **Pass a bounding box:** the extract's own data bbox can be far larger than the region, because
  long boundary and coastline ways extend beyond it.
- **No users in the snapshot:** a Geofabrik public extract has no user data, so only edits from
  the diffs carry users.
