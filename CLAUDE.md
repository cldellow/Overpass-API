# Overpass API fork: working rules

This is cldellow's fork, for exploring performance and robustness changes. The production
servers (overpass-api.de) run `fossgis/Overpass-API`; its `master` (`upstream/master`) is the baseline.
This fork's `master` only adds fork-specific files (this file, the README); never base changes on it.

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
- Use one branch per change, from `upstream/master`, so fork-specific files never enter a change.
  PRs still target this fork's `master`. A dependent PR targets its base branch and says so at
  the top.
- Start a PR description with `Fixes cldellow/Overpass-API#N.`, one line per issue it fixes.
- Label every PR by kind: `query-perf` for query performance changes, `import-fix` for import
  changes, `docs` for documentation only. Also add `format-change` if it changes the on-disk
  format. Give an import change's issues `import-fix` too. Ask if unsure.
- PR descriptions cover the change, the impact on the benchmark queries and on the prompting query
  versus `upstream/master`, testing, and limitations or open questions.
- Descriptions and commit messages must stand alone. Define every query you cite (full text, or a
  table of the benchmark queries) in the same text. No `…` in place of query text.
- Keep `all-changes` (every performance change, cherry-picked onto `upstream/master`) and its PR current.
  Its PR tracks `upstream/master` against `all-changes` for the benchmark queries and each commit's
  prompting query.
- Import changes (`update_database`, `update_from_dir` and the updaters in `osm-backend/`) never go
  into `all-changes`, so performance work isn't affected by them. Keep `better-imports` (every import
  change, cherry-picked onto `upstream/master`) and its PR current instead. Import changes get their
  own issues and PRs like any other change.

## Testing changes

The tools are in `bench/` on the `bench-tools` branch; see `bench/README.md`. The test database is
the Geofabrik Nova Scotia extract, imported with `--meta` (convert PBF to XML with `osmium`).
Databases with history, built from a full-history extract, need a `better-imports` build of
`update_database`; `upstream/master` imports history incorrectly. Query with the build under test.

- **Performance:** run `bench/compare.py`. Don't re-time the baseline; it is cached. Use
  `--no-cache` (alternating runs) when the expected difference is under about 25%: separately
  timed runs can drift by up to about 20%.
- **Correctness:** run `bench/fuzz.py` against the baseline. Run a build against itself first to check
  that outputs are deterministic.
- **Pruning or skipping changes** also need long-limit re-runs of queries that time out on
  the baseline, and a mutation test where practical. Before trusting a clean mutation result,
  confirm the mutant differs from the change on at least one query.
- **Memory:** for any new allocation, state its bound and report peak memory (RSS) against the baseline.
  Follow the existing accounting: only result sets count toward `[maxsize:]`, and auxiliary
  structures are not counted. Counting new ones would change which queries fail.
- **Degenerate queries:** use `fuzz.py --report` to find them, and open an issue per cause.
- **Reporting:** state the dataset, cache state and machine for every number. Report skipped or
  unverified cases.

## Confidential data

Some test data, such as a Geofabrik full-history extract (`.osh.pbf`), is licensed on condition
that it isn't shared and that its personal data isn't disclosed. For such data and anything
derived from it (databases, diffs, fuzz vocabularies, query outputs):

- Never upload, commit, publish or link it, or its download URL, anywhere: not in issues, PRs,
  comments, commit messages, branches or artifacts. Keep it on the session machine.
- Never put user names, user ids or changeset ids from it in anything written, including queries
  cited in issues and PRs. Report results in aggregate, and use placeholders (e.g. `(uid:N)`)
  where a query's shape matters.
- Committed tools may read such data, but must not embed values from it.

## Building

Use one git worktree and build directory per branch, so long runs keep their binaries:

```bash
cd src && mkdir -p m4 && autoreconf -i && mkdir -p ../build && cd ../build
../src/configure --prefix="$PWD/install" CXXFLAGS="-O2 -g" --enable-lz4 && make -j"$(nproc)" install
```
