# Overpass API fork: working rules

This is cldellow's fork, for exploring performance and robustness changes. The production
servers (overpass-api.de) run `fossgis/Overpass-API`; its `master` is the baseline.

## GitHub

- `origin` = `cldellow/Overpass-API`; `upstream` = `fossgis/Overpass-API`.
- Open PRs and issues **only** on `cldellow/Overpass-API`, never on fossgis, drolbr or any other
  repo. For upstream, give a compare link
  (`https://github.com/fossgis/Overpass-API/compare/master...cldellow:Overpass-API:BRANCH`).
- Never ping upstream: no `@mentions`, no `owner/repo#N` references to other repos, no maintainer
  names.

## Scope

- The fossgis fork accepts only changes that are simple to review, don't change the query API,
  and don't change the on-disk format. Prefer those.
- Durability work is out of scope for now.

## Issues and PRs

- Open one issue per topic explored, even if a PR already fixes it.
- Use one branch per change, from `master`. A dependent PR targets its base branch and says so at
  the top.
- Start a PR description with `Fixes cldellow/Overpass-API#N.`, one line per issue it fixes.
- Label every PR `no-format-change` or `format-change`. Ask if unsure.
- PR descriptions cover the change, the impact on the benchmark queries and on the prompting query
  versus `master`, testing, and limitations or open questions.
- Descriptions and commit messages must stand alone. Define every query you cite (full text, or a
  table of the benchmark queries) in the same text. No `…` in place of query text.
- Keep `all-changes` (every change, cherry-picked onto `master`) and its PR current. Its PR tracks
  `master` against `all-changes` for the benchmark queries and each commit's prompting query.

## Testing changes

The tools are in `bench/` on the `bench-tools` branch; see `bench/README.md`. The test database is
the Geofabrik Nova Scotia extract, imported with `--meta` (convert PBF to XML with `osmium`).

- **Performance:** run `bench/compare.py`. Don't re-time `master`; the baseline is cached. Use
  `--no-cache` (alternating runs) when the expected difference is under about 25%: separately
  timed runs can drift by up to about 20%.
- **Correctness:** run `bench/fuzz.py` against `master`. Run a build against itself first to check
  that outputs are deterministic.
- **Pruning or skipping changes** also need long-limit re-runs of queries that time out on
  `master`, and a mutation test where practical.
- **Memory:** for any new allocation, state its bound and report peak memory (RSS) against `master`.
  Follow the existing accounting: only result sets count toward `[maxsize:]`, and auxiliary
  structures are not counted. Counting new ones would change which queries fail.
- **Degenerate queries:** use `fuzz.py --report` to find them, and open an issue per cause.
- **Reporting:** state the dataset, cache state and machine for every number. Report skipped or
  unverified cases.

## Building

Use one git worktree and build directory per branch, so long runs keep their binaries:

```bash
cd src && mkdir -p m4 && autoreconf -i && mkdir -p ../build && cd ../build
../src/configure --prefix="$PWD/install" CXXFLAGS="-O2 -g" --enable-lz4 && make -j"$(nproc)" install
```
