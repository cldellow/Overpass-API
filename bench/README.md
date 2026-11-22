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
- **Peak memory:** `--rss` adds each build's peak RSS, from one extra untimed run per query under
  GNU time (`apt-get install time`). The timed runs are unchanged, and the baseline's RSS is cached
  with its times. GNU time is needed because Python starts children with vfork or fork, and Linux
  carries the parent's peak RSS into the child's `ru_maxrss`, so measuring from Python directly would
  report the Python process's memory for small queries.
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

- **Tested:** `python3 bench/test_fuzz.py [--bin BUILD/bin]` checks, with a synthetic vocabulary,
  that each feature set still generates every construct it should (`poly:`, both area-by-id forms,
  `[bbox:]`, `around`, each out mode, history and user filters, and so on), that generation is
  reproducible, and, with `--bin`, that the queries parse (`osm3s_query --dump-xml`, no database
  needed). Run it after changing the generator; it takes about half a minute.
- **Reproducible:** query N of seed S depends only on S, N and the vocabulary file (the run prints
  the vocabulary's hash), not on `--jobs` or on which other queries ran.
- **Reusable:** `--a` and `--b` take any two builds, so the same seeds can be replayed against every
  change.
- **Import changes:** `--db-b` runs `--b` against a second database. With the same build for `--a`
  and `--b`, this compares two databases, e.g. one imported by the baseline and one by a changed
  `update_database`, or the same file imported with two `--flush-size` values.
- **Failures** are written to `--out-dir` as `<seed>-<index>/{query.ql,a.out,b.out}`, and the exit
  status is 1.
- **Spatial filters:** bboxes, the global `[bbox:]` setting, `around` points and `poly:` polygons
  (star-shaped, 3 to 400 vertices, double or single quotes, as client tools send them).
- **Optional statement families:** `--features areas,history,users,complete` adds area queries
  (`area` by name or by id in both the `area(id:N)` and `area(N)` forms, `is_in`, `map_to_area`,
  `pivot`), history (`[date:]`, `[diff:]`, `[adiff:]`, `newer`,
  `changed`, `retro`, `timeline`), user filters and `complete` loops. Use them only on a database
  that supports them: areas need the area rules run (`osm3s_query --rules < rules/areas.osm3s`),
  and history and users need a database built with `--keep-attic` from diffs with metadata.
  `fuzz_vocab.py --osc-dir DIR` adds the users and edit times from such diffs to the vocabulary.
- **Errors count:** error messages on stderr (and in the output) are part of each outcome, and the
  summary lists the most common ones, so invalid generated queries are visible rather than silently
  matching.
- **Lines naming the build** (generator/version) are ignored. Runs that hit the timeout or the memory
  limit are counted as skipped, not as differences.
- **Finding memory-hungry queries:** with `--rss` as well as `--report`, each row also gets both builds'
  peak RSS (`rss_a_kb`, `rss_b_kb`, `rss_b_over_a`). Every run then goes through GNU time.
- **Finding slow or degenerate queries:** `--report FILE.csv` writes one row per query with its
  status, the CPU time on each build, the output size and the CPU time per KB of output. Sort it to
  find queries that time out, cost a lot for little output, or got slower on the build under test,
  then reprint them with `--show`.

## Property tests on synthetic data

`props/props.py` generates small synthetic databases, runs queries on them and checks every
result. Where `fuzz.py` replays random queries on real data, this targets the configurations real
data almost never contains: points on or 1-2 units (1e-7 degrees) off polygon edges and vertices,
segments parallel to an axis 0-3 units apart, shared endpoints, and data straddling index tile
boundaries, at four scales from a few units to several tiles.

```bash
# Check a build against upstream: properties, plus byte-identical output.
python3 bench/props/props.py --bin NEW/build/bin --baseline UPSTREAM/build/bin --seeds 1-60

# Check upstream against itself (outputs must be deterministic).
python3 bench/props/props.py --bin UPSTREAM/build/bin --baseline UPSTREAM/build/bin --seeds 1-60

# Print one seed's data and queries.
python3 bench/props/props.py --seeds 7 --show
```

- **Only the binaries:** the tests use `update_database` and `osm3s_query`, nothing internal, so
  the same seeds replay unchanged against upstream and every branch. A build directory after
  `make bin/osm3s_query` is enough for the candidate; the importing build also needs
  `make bin/update_database`.
- **Same database for both builds:** each seed is imported once, by the baseline's
  `update_database` (or `--import-bin`), with `--meta`, and the area rules are run on it. Both
  builds then query the same files. Use `--import-bin` with the candidate for changes to the
  on-disk format; import and area-creation changes themselves aren't tested this way.
- **What is checked:** each query's result against an answer computed from the generated data:
  bbox, tag, id, recursion, relation members, set operations, `uid`/`user`/`newer` exactly;
  `poly:`, areas, `is_in` and `around` with a margin (objects clearly inside must be returned,
  objects clearly outside must not; objects within 2 units of the boundary, or within 0.5 m plus
  0.1% of an `around` radius, aren't checked). With `--baseline`, every output must also be
  byte-identical to the baseline's, which covers the boundary cases the margins leave out.
- **Output:** the queries above print only type and id. Separate checks print whole outputs:
  `out meta` in XML and JSON (every node's coordinates, metadata and tags), `out meta geom` and
  `out bb` of ways and `out geom` of relations (node lists, member geometry and bounds), each
  against the generated data; `out center`, CSV with tag columns, JSON geometry and `out skel qt`
  are compared with the baseline only. To exercise output formatting, each seed also has nodes
  at extreme coordinates (0, one unit either side of 0, next to the poles and the antimeridian),
  and tag values and user names that need escaping (`&`, `<`, quotes, newlines, tabs, non-ASCII
  text, a 300-character value, an empty value).
- **History:** with `--history-import-bin BETTER_IMPORTS/build/bin`, each seed also gets a history
  database: the seed's data as version 1 in 2019, then three rounds of edits in 2020-2022 (nodes
  moved, some by a single unit; tags changed; nodes and ways deleted and created; way node lists
  and relation members changed), imported as one full-history file with `--meta --keep-attic`.
  `upstream/master` imports full history incorrectly, so use a `better-imports` build for the
  import. At twelve dates (before the data, at each edit, a second either side, in between,
  after), `[date:]` queries are checked exactly: bbox, tag, id, `uid`, `node(w)`, `way(bn)`,
  `way(r)`, `>` from ways and relations, and `out meta` and `out meta geom` (versions, node lists
  and coordinates as of the date); `way(bbox)` and `poly:` with a margin. Also `changed` (an
  element counts if a version falls in the interval; a way also counts if one of its nodes
  moved then, but not for a node's tag edit), and `uid_touched`/`user_touched` (any version by
  the user, also one after a `[date:]`, which is what upstream does). `[diff:]` and `[adiff:]`
  outputs are compared with the baseline only.
- **Fixes:** where a check has an exact expected answer and the candidate gets it right while
  the baseline doesn't, a difference in output is counted as fixed (the last column), not as a
  failure. A branch that fixes an upstream bug shows up there.
- **Failures on the baseline too** are counted separately and don't fail the run: they're
  upstream behaviour, not a regression. Expect some in the `poly ways` and `area ways` rows:
  upstream misses ways that cross a polygon edge when `uint32` latitudes wrap around in
  `ordered_intersects_inner`, and `way(area)` drops a way that touches the area's border if it
  also crosses the interior. With history, `date rel >` fails occasionally: at a past date,
  `relation; >;` also returns nodes of a member way's current version when they're in another
  index tile. And `rel set uid_touched` always fails on upstream, which filters a set's relations
  by the touched criterion only when the set also holds nodes (fixed on this fork; see
  "Fixes" above).
- **Failures** are printed with their seed and query, and with `--out-dir` written to
  `OUT_DIR/<seed>-<check>/{data.osm,query.ql,candidate.out,baseline.out,problems.txt}`. The exit
  status is 1 if any check fails on the candidate but not the baseline, or any output differs.
- **Reproducible:** a seed's data and queries depend only on the seed and the script (its hash
  is printed).
- **Speed:** 60 seeds (about 5,400 queries per build) take about two and a half minutes on 4 cores;
  with history (about 17,000 more), about six and a half.

### Does a passing run mean anything? (`mutate.py`)

`props/mutate.py` breaks each change on purpose and checks that `props.py` notices. Each mutant
in `props/mutants-all-changes.json` is one exact text replacement in the `all-changes` sources,
a plausible bug in the logic one commit added (an off-by-one, a dropped case, a wrong index).
For each, the script applies it, rebuilds `osm3s_query`, runs `props.py` against the baseline
and restores the file.

```bash
python3 bench/props/mutate.py --src ALL_CHANGES --build ALL_CHANGES/build \
    --baseline UPSTREAM/build/bin --history-import-bin BETTER_IMPORTS/build/bin \
    --mutants bench/props/mutants-all-changes.json --seeds 1-40
```

- **Use a dedicated checkout:** the source files and the build are modified while it runs (each
  file is restored afterwards, also on an error), and the build is rebuilt from the restored
  sources at the end. Don't run anything else against that build meanwhile.
- **Caught** means the mutant's summary table differs from the unmutated tree's: more failures,
  more output differences, or fewer fixes. Comparing with the unmutated run, not only with the
  baseline, also catches a mutant that undoes a bug fix, whose output matches the baseline again.
- **Reading the result:** a mutant that isn't caught marks a change whose correctness the
  checks don't cover, or an equivalent mutant (one that can't change any result; the JSON's
  `note` says so where that's expected).
- **Keeping it current:** a mutant whose text no longer matches is reported as not applicable.
  When a commit in `all-changes` changes, update its mutant.
- **Time:** a rebuild plus a `props.py` run per mutant, so a few minutes each.


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
