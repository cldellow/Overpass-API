# Conformance corpus (work in progress)

Goal: a small, fixed set of queries that covers as much of the query engine as a large pool of
generated queries does, to be re-run on every change and compared with `upstream/master`.

## Pieces

- `cov_rt.c`: a minimal SanitizerCoverage runtime. At exit it writes each hit edge of the
  8-bit counters, with an AFL-style hit-count bucket, to `$COV_OUT`. With `pc-table`, and
  `$COV_PCS` set, it writes each edge's PC once, for mapping edges to source with
  `llvm-symbolizer`.
- `coverage.py`: runs queries (from `fuzz.py`'s generator, or `*.ql` directories) on the
  instrumented binary and writes one JSON line per query: status, normalised output digest (as
  `fuzz.py`), CPU and features (`edge * 16 + bucket`).
- `select.py`: lazy greedy weighted set cover (new features per CPU second) and the coverage
  curve.

## Building the instrumented binary

Static, so there is one instrumented module; `-fno-sanitize-link-runtime` keeps clang from
linking the sanitizer runtimes:

```bash
clang -O2 -c bench/corpus/cov_rt.c -o cov_rt.o
cd UPSTREAM/src && mkdir -p m4 && autoreconf -i && mkdir -p ../build && cd ../build
../src/configure CC=clang CXX=clang++ CFLAGS=-O1 \
    CXXFLAGS="-O1 -g -fsanitize-coverage=inline-8bit-counters,pc-table" \
    LDFLAGS=-fno-sanitize-link-runtime LIBS=$PWD/../../cov_rt.o --enable-lz4 --disable-shared
make -j"$(nproc)" bin/osm3s_query
```

## Pilot (upstream/master 8edf11b, Nova Scotia with areas, 4-core VM)

3,027 queries (1,500 plain and 1,500 `areas,complete` from `fuzz.py` seed 1, plus `queries`,
`queries-areas` and `queries-tools`) ran in 2 min 42 s. They reach 20,928 of the binary's 180,396
edges (90,680 features with hit-count buckets).

| Share of the pool's | features: queries (CPU) | edges: queries (CPU) |
|---|---:|---:|
| 90% | 305 (11.8 s) | 50 (1.3 s) |
| 95% | 465 (23.9 s) | 105 (3.2 s) |
| 99% | 838 (62.2 s) | 508 (28.2 s) |
| 100% | 1,091 (240.7 s) | 1,082 (130.7 s) |

The 1,091 selected queries give the same output digest in two runs on the normal build and on the
instrumented one.

Edges level off by about 1,000 queries per feature set, so the pool, not compute, is the limit.
Barely reached: evaluators (`binary_operators.h` 10/723 edges, `aggregators.cc` 1/275,
`set_prop.cc` 30/470), `localize.cc` 1/597, `for.cc` 1/187, `make_area.cc` 1/155,
`explicit_geometry.cc` 1/309, `output_custom.cc` 0/335; history-only code (`user.cc`, `changed.cc`,
`set_comparison.cc`, `diff_set.h`) needs the history database. Upstream's 137
`osm-3s_testing/input/osm3s_query_*` queries add 781 edges (+3.7%).
