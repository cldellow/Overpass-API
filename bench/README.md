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
