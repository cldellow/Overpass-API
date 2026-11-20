# Working on this fork

This is cldellow's fork of the Overpass API, used to explore making it faster and more robust.
The production servers behind overpass-api.de run `fossgis/Overpass-API`, so that is the
baseline. Changes are prepared here so they can be proposed there.

## Repositories and where things go

- **`origin` is `cldellow/Overpass-API`** (this fork). Push working branches here.
- **`upstream` is `fossgis/Overpass-API`.** Its `master` is the baseline for builds and benchmarks.
  `drolbr/Overpass-API` is the original project.
- **Never open PRs or issues against `fossgis/Overpass-API`, `drolbr/Overpass-API`, or any other
  repository you don't own.** Open them only on `cldellow/Overpass-API`. To propose something
  upstream, give a compare link (e.g.
  `https://github.com/fossgis/Overpass-API/compare/master...cldellow:Overpass-API:BRANCH`) and
  let the user open it.
- **Never ping or create noise for upstream maintainers:**
  - no `@mentions`
  - no references to issues or PRs in other repositories (`owner/repo#123`), since GitHub
    back-links those
  - no maintainer names in issue, PR or commit text

  References to issues and PRs in this fork are fine.

## What changes are in scope

The fossgis fork accepts, for now, only changes that:

- **are not overly complex**, because somebody has to review them
- **don't change the query API**
- **don't change how the database is stored on disk**

Prefer changes that meet all three. Format-changing ideas (finer spatial keys, delta encoding,
coordinates in the tag index, new compression methods) can still be explored, but label them
clearly. Durability work (fsync and the like) is out of scope for now.

## Issues, PRs and commits

- **One issue per thing explored:** each problem, bottleneck or degenerate query gets its own
  issue in this fork, including topics whose fix is already in a PR.
- **One branch and PR per change**, based on `master`. A PR that depends on another targets that
  branch (a stacked PR) and says so at the top.
- **Link fixes:** when a PR fixes an issue, its description starts with
  `Fixes cldellow/Overpass-API#N.`, one line per issue, so the issues view shows what is pending.
  (Auto-linking only works for PRs that target `master`.)
- **Label every PR** with `no-format-change` or `format-change`, depending on whether it changes
  what is written to disk. Ask if unsure.
- **PR descriptions** concisely cover:
  - the change
  - its impact on the benchmark queries and on the query that prompted it, measured against
    `master`
  - testing (benchmark output equality, differential fuzzer results)
  - limitations and open questions (e.g. unknown production compression settings or query mix)
- **Everything must stand alone.** Issues, PR descriptions and commit messages may use a short
  label like "query 2420" or "q07" only if the same text defines it somewhere: the full query
  text, or a table of the benchmark queries. Collapsible `<details>` sections are fine for long
  queries. Don't use `…` in place of real query text unless the full query appears elsewhere in
  the same text.
- **Keep the `all-changes` branch current:** it holds every change commit, cherry-picked onto
  `master`. Its PR tracks the performance of the benchmark queries and of each commit's prompting
  query, `master` against `all-changes`. Update the branch and that PR when a change is added or
  revised.
- **Attribution:** end commit messages and PR descriptions with the attribution lines the session
  provides.

## Benchmarking and correctness

The tools live on the `bench-tools` branch under `bench/`; see `bench/README.md` there.

- **Test database:** the Geofabrik Nova Scotia extract (mirrored at
  `https://public.hikeratlas.com/pbfs/raw/nova-scotia.osm.pbf`), imported with
  `osmium cat nova-scotia.osm.pbf -f osm -o - | bin/update_database --db-dir=DB/ --meta`.
  `update_database` reads OSM XML, not PBF.
- **Performance:** run `bench/compare.py` (CPU time per query, plus output equality).
  - Don't re-benchmark `master` needlessly: the baseline's results are cached by default.
  - When a difference is overwhelming, precision matters little.
  - Use `--no-cache` (alternating runs) only for close calls.
- **Correctness:** run `bench/fuzz.py`, the differential fuzzer, comparing a change against
  `master`.
  - Queries are reproducible from (seed, index) and the vocabulary built by
    `bench/fuzz_vocab.py`.
  - Run the same build against itself first to check that outputs are deterministic.
  - A change that prunes or skips work should get extra checks: the queries `master` times out
    on, re-run with long limits; and, where practical, a mutation test (a deliberately broken
    build that the fuzzer must catch).
- **Degenerate queries:** use `fuzz.py --report` to find queries that time out or cost a lot of
  CPU for little output. Open an issue for each distinct cause.
- **Report honestly:** state the dataset, warm or cold cache, and machine for every number.
  Report skipped or unverified cases rather than leaving them out.

## Building

```bash
cd src && mkdir -p m4 && autoreconf -i
mkdir -p ../build && cd ../build
../src/configure --prefix="$PWD/install" CXXFLAGS="-O2 -g" --enable-lz4
make -j"$(nproc)" install
```

Needs `g++`, `autoconf`, `automake`, `libtool`, `libexpat1-dev`, `zlib1g-dev` and `liblz4-dev`;
`osmium-tool` for imports. Use a separate git worktree and build directory per branch, so that
long benchmark or fuzz runs keep using the binaries they started with.

## Communicating

- Refer to files with absolute paths, e.g. `/home/user/overpass-api/src/...:LINE`, so the links
  open.
- Write user-facing files only inside the working directory or the scratchpad.
