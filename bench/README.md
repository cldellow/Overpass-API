# Benchmark tools

`compare.py` runs the same queries against two Overpass builds on one database and
reports the CPU time (user + system) of each `osm3s_query` run, plus whether the two
builds produce identical output.

```bash
python3 bench/compare.py --a BASELINE/install/bin --b CANDIDATE/install/bin \
    --db /path/to/db --reps 7 bench/queries/*.ql
```

- Runs alternate between the builds so background noise affects both alike, after one
  untimed warm-up run per build.
- Output equality ignores lines that name the build (generator/version).
- Exit status is 1 if any output differs.

`queries/` holds the sample queries; their bounding boxes are in Halifax, Nova Scotia, so they
expect a database built from a Nova Scotia extract.

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
- **Failures** are written to `--out-dir` as `<seed>-<index>/{query.ql,a.out,b.out}`, and the exit
  status is 1.
- **Lines naming the build** (generator/version) are ignored. Runs that hit the timeout or the memory
  limit are counted as skipped, not as differences.
