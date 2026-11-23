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
- **Small differences:** `--paired` (which implies `--no-cache`) treats each repetition as a pair
  of runs, A then B or B then A in turn, and adds a line per query: the mean per-pair B/A ratio
  with a 95% confidence interval, in how many pairs B was faster, and a two-sided sign test. A
  median with a min–max spread can't settle a difference of a few percent, because single runs
  have outliers; 25 pairs (`--reps 25`) can. With `--json`, each query's record also gets
  `paired_stats`.

  ```bash
  python3 bench/compare.py --a BASE/install/bin --b NEW/install/bin --db DB --paired --reps 25 QUERY.ql
  ```
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

## Timing on GitHub Actions

The `timing` workflow (`.github/workflows/timing.yml` on `master`) runs `compare.py --no-cache --json` on several runners and combines them: per query, the median over runners of each runner's B/A ratio. It runs on pull requests into `master` that change `src/`, and by hand with a ref, the number of runners, the runner type (ARM by default, `ubuntu-24.04` for x86) and a mode.

- **Noise floors:** `timing-floors.json` holds each query's noise floor per runner type: the largest deviation from 1.0 of any single runner's A/A ratio in a calibration run (mode `calibrate`, 10 runners, 5 alternating runs per query per build). The summary calls a median B/A ratio faster or slower only when it's further from 1.0 than the query's floor, and "within noise" otherwise.
- **Current floors** (calibrated 2026-11-22):
  - ARM (`ubuntu-24.04-arm`, all Neoverse-N2): median 0.5%, worst `q03` 2.2%.
  - x86 (`ubuntu-24.04`, a mix of AMD EPYC 7763, Xeon Platinum 8573C and Xeon 6975P-C): median 0.7%, worst `q12` 4.6%.
- **Recalibrate** after a runner image change, or when queries are added: run the workflow with mode `calibrate` and 10 runners, download its `timing-summary` artifact, and replace that runner type's entry (`calibration_run`, `date`, `cpus` and `floors`). A query without a floor is shown as "no floor".

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
  needed). It also runs `bench/test_fuzz_cache.py`, which tests the baseline cache with a fake
  `osm3s_query`. Run it after changing the generator or the cache; it takes about a minute.
- **Reproducible:** query N of seed S depends only on S, N and the vocabulary file (the run prints
  the vocabulary's hash), not on `--jobs` or on which other queries ran.
- **Reusable:** `--a` and `--b` take any two builds, so the same seeds can be replayed against every
  change.
- **Baseline cache:** the baseline's (`--a`'s) outcomes are cached in `--cache-dir` (default
  `~/.cache/overpass-bench/fuzz/`), so replaying seeds against another change runs only `--b`.
  - **Key:** a hash of `--a`'s `osm3s_query` binary, a fingerprint of the database's data files
    (name, size and modification time of each `*.bin`, `*.idx` and `*.map` file and the
    replication markers, so an import or update invalidates it, but `transactions.log` doesn't),
    `--timeout`, and the query text. The vocabulary and `--features` only decide the query text,
    so they need no separate place in the key.
  - **Not cached:** timeouts and memory-limit failures, which depend on load. They run again.
  - **Checked on differences:** when a query differs and its baseline came from the cache, the
    baseline runs again, to write `a.out` and to confirm the cached outcome still holds. If it
    doesn't (an undetected database change, or a baseline that isn't deterministic), the entry is
    replaced, the fresh outcome decides the verdict, and the summary counts it as stale.
  - **Reporting:** the summary says how many outcomes were reused. `--report` adds an `a_cached`
    column at the end; a cached row's `cpu_a_ms` and `rss_a_kb` come from the run that filled the
    cache. With `--rss`, entries without a peak RSS run again.
  - **Flags:** `--no-cache` runs the baseline every time and leaves the cache alone, and
    `--clear-cache` empties it first. With the same build as `--a` and `--b`, a cached run compares
    fresh outputs against earlier ones, which checks determinism across runs.
  - **Licensed data:** entries hold digests and sizes, not outputs, but entries from a database
    derived from licensed data (such as a full-history extract) stay on the machine, like the
    database.
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
- **Evaluators:** `--features evaluators` (any database) adds `convert`, `make`, `for` (also over
  `keys()`), `if`/`else` and richer `(if:)` filters, from a typed expression grammar in
  `fuzz_eval.py`: every function the parser registers, arithmetic, comparison, logical and ternary
  operators, list (`lrs_*`), string and date functions, geometry constructors (`pt`, `lstr`,
  `poly`, `hull`, `trace`, `center`, `gcat`), `per_member` and `per_vertex`, and set aggregates
  (`u`, `set`, `sum`, `min`, `max`, `count`) combined with operators. About 1% of the queries carry
  a deliberately malformed expression, for the error paths. Seeds without it are unchanged.
- **Output formats:** `--features output` (any database) varies the settings line and the `out`
  statements (`fuzz_output.py`): `[out:xml]`, `[out:json]`, `[out:csv(...)]` with every `::` field
  (the `count:*` ones quoted, as the parser needs), header line on or off and several separators,
  and `[out:popup(...)]` with categories of tag filters and title keys; `[timeout:]`, `[maxsize:]`
  and `[bbox:]`; and every `out` mode (`ids`, `skel`, `body`, `tags`, `meta`, `count`), geometry
  (`geom`, `bb`, `center`), order, `noids`, limit and bbox. `out quirks`, which the parser accepts
  but the print statement rejects, appears in about 1% of statements. `[out:custom]` is left out:
  it needs templates in the database directory.
- **More statements:** `--features statements` (`fuzz_more.py`) adds `local` (plain, `ll`, `llb`),
  tag-only queries with every tag-filter form (regex values, regex keys, `,i`, alternations,
  negations), polyline `around`, member filters with roles, recursion on `_`, and the `foreach`
  variants. With `areas`, it adds area filters by id (`(area:N)`); with `history`, `compare` in
  diff queries (plain, `(delta:...)`, with a block).
- **All of them:** `--features evaluators,output,statements` combines the three; add `areas,complete`
  on a database with areas, or `history,users,complete` on one with history. Seeds of feature sets
  without these three are unchanged.
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

### Deadline audit

`deadline_audit.py` looks for loops that don't check the query's time limit often enough. It runs
`fuzz.py`'s queries on one build with `[timeout:T]` in place of their own limit, and reports those
whose wall time exceeds T by more than `--slack` seconds, with the statement where the timeout was
finally noticed (from the `Query timed out` error), or `killed` after `--kill` seconds:

```bash
python3 bench/deadline_audit.py --bin BASE/install/bin --db DB --vocab vocab.json --seed 8 --count 2000 --limit 2
```

It prints query indices, not query text, so it's safe with private vocabularies; reprint a query
with `fuzz.py --show`. Run it against a slow build (`upstream/master`): on a fast one few queries
reach the limit at all.

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
- **Consistency:** pairs of queries that must agree, so no expected answer is needed and bugs
  the baseline shares are caught too (counted in its column): a box or a rectangular polygon
  against the union of its two halves (split on a tile boundary where possible); a polygon
  against itself reversed and starting at another vertex; the same filters in different orders;
  `[bbox:]` against an inline bbox; `around` a point against `around` a node at that point;
  `way(bn)` of the nodes in a box within `way(bbox)`; a way within `way(around.s:0)` of itself;
  `around` a set of relations against the union of `around` each one, collected in a `foreach`
  (the same statement run again with another relation); and, on the history database at each
  date, `around` a relation against `around` its direct member nodes and ways, preferring
  relations edited after the date.
  And translation: each seed is also imported shifted by an offset that isn't a multiple of the
  tile size, and bbox, `poly:`, area and `is_in` queries must return the same elements on both.
  Region comparisons leave out elements that come within one unit of the boundary without being
  clearly inside, since the geometry tests round crossing points from one end of a segment:
  for those, upstream's result can depend on vertex order and on where tiles cut the segments.
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
  "Fixes" above). The consistency rows `poly reversed ways`, `split poly ways`, `shifted poly
  ways` and `shifted area ways` fail occasionally on upstream (mostly the `uint32` wraparound;
  a few are a way through a polygon vertex that is lost after shifting), and `shifted is_in`:
  `is_in` returns a closed multipolygon member way for some points kilometres outside it.
- **Failures** are printed with their seed and query, and with `--out-dir` written to
  `OUT_DIR/<seed>-<check>/{data.osm,query.ql,candidate.out,baseline.out,problems.txt}`. The exit
  status is 1 if any check fails on the candidate but not the baseline, or any output differs.
- **Reproducible:** a seed's data and queries depend only on the seed and the script (its hash
  is printed).
- **Speed:** 60 seeds run about 9,200 checks on current data and 17,000 with history (a
  consistency check runs two queries), in about seven minutes on 4 cores.

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
