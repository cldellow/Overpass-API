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
- Start a PR description with `Fixes #N.`, one line per issue it fixes.
  Tracking PRs (`all-changes`, `better-imports`) use one line: `Fixes #3, fixes #4.` Repeat the
  keyword, and keep the list: GitHub ignores `Fixes` in stacked PRs, so only the tracking PR links them.
- Label every PR by kind: `query-perf` for query performance changes, `query-memory` for changes
  that lower a query's peak memory, `import-fix` for import changes, `docs` for documentation
  only. A change that lowers both runtime and memory gets both `query-perf` and `query-memory`.
  Also add `format-change` if it changes the on-disk format, and `bug` if it fixes wrong results.
  Give the issues the same labels. Ask if unsure.
- PR descriptions cover the change, the impact on the benchmark queries and on the prompting query
  versus `upstream/master`, testing, and limitations or open questions.
- Descriptions and commit messages must stand alone. Define every query you cite (full text, or a
  table of the benchmark queries) in the same text. No `…` in place of query text.
- Keep `all-changes` (every performance and memory change, cherry-picked onto `upstream/master`) and
  its PR current. Its PR tracks `upstream/master` against `all-changes` for the benchmark queries
  and each commit's prompting query.
- Import changes (`update_database`, `update_from_dir` and the updaters in `osm-backend/`) never go
  into `all-changes`, so performance work isn't affected by them. Keep `better-imports` (every import
  change, cherry-picked onto `upstream/master`) and its PR current instead. Import changes get their
  own issues and PRs like any other change.

## Code comments

Upstream prefers few inline comments: explain a change in its commit message, which `git blame`
finds. Add a comment only where the code would mislead a reader without it, such as an invariant
the code relies on but doesn't show, and keep it to a line or two. Don't restate the commit
message, describe what the code plainly does, or record measurements, alternatives tried or
history.

## Proposing a change upstream

Before giving the compare link for a change upstream may merge, prepare its branch:

- **Comments** follow "Code comments" above; trim older code to it as it goes upstream.

- **One commit**, rebased onto `upstream/master`. Squash iterations so the reviewer reads only
  the design that ships. Rebuild with `git cherry-pick` or the diff from the merge base
  (`git diff upstream/master...BRANCH`), never `git diff upstream/master BRANCH`: that also
  reverts whatever upstream merged since the branch's base.
- **Author** `Claude <noreply@anthropic.com>`; the only trailer is
  `Co-Authored-By: Colin Dellow <cldellow@gmail.com>` (no session link, no Claude co-author line).
- **Subject** says what the change does, not how (`out count, around: don't copy the input set`).
- **Body:** the cause, the fix naming the functions it touches, and one or two measurements with
  their full query text and the dataset, cache state and machine. Use the same numbers as the PR
  description. Leave claims such as "outputs are unchanged" or "no new allocations" to the PR
  description.
- **Upstream PR description:** no fork-relative references (`#N` links to upstream's own
  issues and PRs there), and no fork-only tools (`bench/...`) a reviewer can't run.
- Show the user the draft message before rewriting the commit.
- **After review:** squash review fixes into the commit they fix and force-push the PR branch.
  Never add fix-up commits to an upstream PR.
- **Catching up:** rebase the PR branch onto `upstream/master`; never merge `upstream/master`
  into it.

## Testing changes

The tools are in `bench/` on the `bench-tools` branch; see `bench/README.md`. The test database is
the Geofabrik Nova Scotia extract, imported with `--meta` (convert PBF to XML with `osmium`).
Databases with history, built from a full-history extract, need a `better-imports` build of
`update_database`; `upstream/master` imports history incorrectly. Query with the build under test.

- **Performance:** run `bench/compare.py`. Don't re-time the baseline; it is cached. Use
  `--no-cache` (alternating runs) when the expected difference is under about 25%: separately
  timed runs can drift by up to about 20%. Under about 10%, use `--paired --reps 25` and judge by
  its confidence interval and sign test, not by medians and spreads. Don't call a change noise, or
  drop it, without a paired measurement: medians misjudged real 4–9% gains as noise.
- **Correctness:** don't run `bench/fuzz.py` locally to check a change; CI runs it. The `fuzz`
  workflow (plain database: plain, `areas,complete`, and `extended`, which adds
  `evaluators,output,statements,poi`) and the `history` workflow (full-history database:
  `history,users,complete`, and the same plus `evaluators,output,statements,poi`) run on every PR into
  `master` that touches `src/`. For a
  stacked PR, or one opened before these workflows existed, dispatch both from `master`:
  `gh api -X POST repos/cldellow/Overpass-API/actions/workflows/fuzz.yml/dispatches -f ref=master
  -f 'inputs[ref]=BRANCH'`, and the same for `history.yml`. Read the verdicts in the `summary` job's
  log, and cite the run and its counts in the PR. A run with differences blocks the PR until
  explained. History differences are in encrypted artifacts: ask the user for them. Run `fuzz.py`
  locally only to investigate a difference, or for query features the workflows don't cover.
- **Property tests:** also run `bench/props/props.py` against the baseline, with
  `--history-import-bin` pointing at a `better-imports` build. The `props` workflow runs it on every
  PR into `master`; trigger it by hand for PRs into other branches. A run with new failures or
  output differences blocks the PR until explained. Failures shared with the baseline don't count.
- **Mutation coverage:** when a change joins `all-changes`, add a mutant for it to
  `bench/props/mutants-all-changes.json` (on `bench-tools`): one exact text replacement that puts a
  plausible bug in the logic the change adds. Check it with
  `bench/props/mutate.py --only COMMIT`. If it isn't caught, add a check to `props.py`, or record in
  the mutant's `note` why it can't be caught (equivalent, or needs data beyond the synthetic sets).
  When an `all-changes` commit changes, update its mutant.
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

Some test data, such as a Geofabrik full-history extract (`.osh.pbf`), carries this condition:
"You must ensure that derived databases and works based on these files are only accessible to
OpenStreetMap contributors." For such data and anything derived from it (databases, diffs, fuzz
vocabularies, `--vars` files, query outputs, fuzz failures and reports):

- Plaintext stays on the session machine or inside a running CI job. Never upload, commit, publish
  or link it, or its Geofabrik download URL, anywhere else, except encrypted (below).
- Never put user names, user ids or changeset ids from it in anything written, including queries
  cited in issues and PRs. Report results in aggregate, and use placeholders (e.g. `(uid:N)`)
  where a query's shape matters.
- Committed tools may read such data, but must not embed values from it.

### In CI

Workflows may process it on GitHub's public runners:

- **Encrypted at rest:** copies encrypted with `age` to `AGE_PUBLIC_KEY` may be stored publicly (R2,
  the Actions cache) and named in workflows. Anything a job saves or uploads is encrypted the same way.
- **The key:** `AGE_PRIVATE_KEY` lives only in secrets. Write it to `$RUNNER_TEMP` with `umask 077`,
  never print it, and delete it after use.
- **Trusted runs only:** guard `pull_request` jobs with
  `github.event.pull_request.head.repo.full_name == github.repository`, never use
  `pull_request_target`, and pin third-party actions by commit SHA.
- **Logs: aggregates only:** counts, verdicts, timings, seeds and query indexes. Never query text,
  raw error messages or outputs.
- **Leak check:** before anything leaves a job, scan it and the job's output for the vocabulary's
  user names and uids, and fail without printing matches.

## Building

Use one git worktree and build directory per branch, so long runs keep their binaries:

```bash
cd src && mkdir -p m4 && autoreconf -i && mkdir -p ../build && cd ../build
../src/configure --prefix="$PWD/install" CXXFLAGS="-O2 -g" --enable-lz4 && make -j"$(nproc)" install
```

`autoreconf -i` also installs `src/ar-lib` and `src/compile`. `upstream/master`'s `src/.gitignore` doesn't
list them (#82), so they show up as untracked files. Never commit them: stage
files by name rather than with `git add -A` or `git add .`, and check `git show --stat` before pushing.
