#!/usr/bin/env python3
"""Compare two Overpass builds on the same database: CPU time per query and output equality.

CPU time is user+system time of the osm3s_query process (from getrusage), which
ignores time spent waiting.

By default the baseline (--a) results are cached in --cache, keyed by a hash of the
baseline's osm3s_query binary, the database path and the query text. Later runs
against the same baseline reuse them and only time the candidate (--b). Changing the
baseline binary, the database or a query invalidates its entry.

With --no-cache, each query runs --reps times per build, alternating A and B so that
background noise affects both builds alike. Use that when the difference is small.

With --paired (which implies --no-cache), each repetition is a pair of runs, A then B or B
then A in turn, and each query also gets paired statistics: the mean of the per-pair B/A
ratios with a 95% confidence interval, in how many pairs B was faster, and a two-sided sign
test. Medians with min-max spreads can't settle differences of a few percent, because single
runs have outliers; the pairs can. Use 25 or more pairs (--reps 25) for differences under
about 10%.

A query may contain placeholders such as {{UID}}, filled in from the JSON object in
--vars. Use them for values that must not be committed, such as user ids from a
licensed extract; bench/pick_vars.py writes such a file from a database.

Usage:
  compare.py --a OLD/bin --b NEW/bin --db DB_DIR [--reps 7] [--no-cache | --paired] [--vars FILE] QUERY.ql [QUERY.ql ...]

With --json FILE, the raw measurements are also written to FILE: per query, every run's CPU
time for each build, the output digests and size, and peak RSS with --rss. Use it to combine
runs from several machines (e.g. the timing workflow).

Exit status is 1 if any query's output differs between the builds.
"""
import argparse
import hashlib
import json
import math
import os
import re
import resource
import statistics
import subprocess
import sys

from osm3s_run import check_rss_support, run_osm3s

# Lines that name the build, so they differ between builds without the data differing.
VOLATILE = re.compile(rb'(generator|Overpass API \d|"version":)')


PLACEHOLDER = re.compile(r'\{\{([A-Z_]+)\}\}')
QUERY_VARS = {}


def query_text(query_path):
    """The query's text, with {{NAME}} placeholders filled in from --vars."""
    with open(query_path) as f:
        text = f.read()
    def fill(m):
        if m.group(1) not in QUERY_VARS:
            sys.exit(f'{query_path}: placeholder {m.group(0)} needs a value in --vars')
        return str(QUERY_VARS[m.group(1)])
    return PLACEHOLDER.sub(fill, text).encode()


def run_query(bindir, db, query_path, rss=False):
    """Run one query; return (cpu_seconds, output_digest, output_bytes, peak_rss_kb or None)."""
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    returncode, stdout, _, peak = run_osm3s(bindir, db, query_text(query_path), rss=rss)
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    cpu = (after.ru_utime - before.ru_utime) + (after.ru_stime - before.ru_stime)
    if returncode != 0:
        sys.exit(f'{query_path}: osm3s_query in {bindir} exited with {returncode}')
    stable = b'\n'.join(line for line in stdout.split(b'\n') if not VOLATILE.search(line))
    return cpu, hashlib.sha256(stable).hexdigest(), len(stdout), peak


def query_sha(path):
    return hashlib.sha256(query_text(path)).hexdigest()


def file_sha(path):
    with open(path, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


def measure(bindir, db, query, reps):
    """Run a query reps times after one warm-up; return times, digests and output size."""
    run_query(bindir, db, query)
    times, digests, size = [], set(), 0
    for _ in range(reps):
        seconds, digest, size, _ = run_query(bindir, db, query)
        times.append(seconds)
        digests.add(digest)
    return {'times': times, 'digests': sorted(digests), 'size': size}


def paired_stats(a_times, b_times):
    """The per-pair B/A ratios' geometric mean with a 95% confidence interval, the number of
    pairs where B was faster, and a two-sided sign test's p-value."""
    logs = [math.log(b / a) for a, b in zip(a_times, b_times) if a > 0 and b > 0]
    n = len(logs)
    if n < 2:
        return None
    mean = statistics.mean(logs)
    half = 1.96 * statistics.stdev(logs) / math.sqrt(n)
    wins = sum(1 for x in logs if x < 0)
    tail = sum(math.comb(n, j) for j in range(0, min(wins, n - wins) + 1)) / 2 ** n
    return math.exp(mean), math.exp(mean - half), math.exp(mean + half), wins, n, min(1.0, 2 * tail)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--a', required=True, help='bin directory of the baseline build')
    parser.add_argument('--b', required=True, help='bin directory of the build under test')
    parser.add_argument('--db', required=True, help='database directory')
    parser.add_argument('--reps', type=int, default=7, help='runs per query per build (default 7)')
    parser.add_argument('--cache', default=os.path.expanduser('~/.cache/overpass-bench/baseline.json'),
                        help='baseline result cache (default ~/.cache/overpass-bench/baseline.json)')
    parser.add_argument('--no-cache', action='store_true',
                        help='time both builds, alternating runs, and ignore the cache')
    parser.add_argument('--paired', action='store_true',
                        help='alternate the order within each pair of runs and report paired statistics '
                             '(per-pair B/A ratio with a 95%% confidence interval, and a sign test); '
                             'implies --no-cache')
    parser.add_argument('--vars', help='JSON file with values for {{NAME}} placeholders in queries')
    parser.add_argument('--rss', action='store_true',
                        help='also report peak memory (RSS) of each build, from one extra untimed run '
                             'per query under GNU time; the baseline\'s is cached with its times')
    parser.add_argument('--json', metavar='FILE',
                        help='also write the raw measurements (every run\'s CPU time per build) to FILE')
    parser.add_argument('queries', nargs='+')
    args = parser.parse_args()
    if args.paired:
        args.no_cache = True
    if args.vars:
        with open(args.vars) as f:
            QUERY_VARS.update(json.load(f))
    if args.rss:
        check_rss_support()

    cache = {}
    if not args.no_cache and os.path.exists(args.cache):
        with open(args.cache) as f:
            cache = json.load(f)
    baseline_sha = file_sha(os.path.join(args.a, 'osm3s_query'))
    db_key = os.path.realpath(args.db)

    rss_header = f'  {"A rss":>9} {"B rss":>9} {"B/A":>5}' if args.rss else ''
    print(f'{"query":28} {"A cpu":>9} {"B cpu":>9} {"B/A":>6}  {"A spread":>15} {"B spread":>15}{rss_header}  output')
    total_a = total_b = 0.0
    differing = []
    reused = 0
    records = []
    for query in args.queries:
        key = f'{baseline_sha}|{db_key}|{query_sha(query)}'
        a_cached = False
        if args.no_cache:
            run_query(args.a, args.db, query)
            run_query(args.b, args.db, query)
            a = {'times': [], 'digests': set(), 'size': 0}
            b = {'times': [], 'digests': set(), 'size': 0}
            for rep in range(args.reps):
                order = ((a, args.a), (b, args.b))
                if args.paired and rep % 2 == 1:
                    order = order[::-1]
                for result, bindir in order:
                    seconds, digest, size, _ = run_query(bindir, args.db, query)
                    result['times'].append(seconds)
                    result['digests'].add(digest)
                    result['size'] = size
            a['digests'] = sorted(a['digests'])
            b['digests'] = sorted(b['digests'])
        else:
            if key in cache:
                a = cache[key]
                reused += 1
                a_cached = True
            else:
                a = measure(args.a, args.db, query, args.reps)
                cache[key] = a
            b = measure(args.b, args.db, query, args.reps)
        rss_cols = ''
        if args.rss:
            # One untimed run per build, after the timed ones; peak RSS varies little between runs.
            if a.get('rss') is None:
                a['rss'] = run_query(args.a, args.db, query, rss=True)[3]
                if not args.no_cache:
                    cache[key] = a
            b['rss'] = run_query(args.b, args.db, query, rss=True)[3]
            ratio = f'{b["rss"] / a["rss"]:5.2f}' if a['rss'] and b['rss'] else '    -'
            mb = lambda kb: f'{kb / 1024:7.1f}MB' if kb else '        -'
            rss_cols = f'  {mb(a["rss"]):>9} {mb(b["rss"]):>9} {ratio}'
        med_a, med_b = statistics.median(a['times']), statistics.median(b['times'])
        total_a += med_a
        total_b += med_b
        same = a['digests'] == b['digests'] and len(a['digests']) == 1
        if not same:
            differing.append(query)
        name = os.path.splitext(os.path.basename(query))[0]
        records.append({'query': query, 'name': name, 'same': same, 'a_cached': a_cached,
                        'alternating': args.no_cache, 'paired': args.paired, 'size': b['size'],
                        'a_times': a['times'], 'b_times': b['times'],
                        'a_digests': a['digests'], 'b_digests': b['digests'],
                        'a_rss_kb': a.get('rss'), 'b_rss_kb': b.get('rss')})
        spread = lambda xs: f'{min(xs)*1000:6.1f}-{max(xs)*1000:6.1f}ms'
        print(f'{name:28} {med_a*1000:7.1f}ms {med_b*1000:7.1f}ms {med_b/med_a:6.2f}  '
              f'{spread(a["times"]):>15} {spread(b["times"]):>15}{rss_cols}  '
              f'{"same" if same else "DIFFERENT"} ({b["size"]} bytes)', flush=True)
        if args.paired:
            stats = paired_stats(a['times'], b['times'])
            if stats:
                ratio, lo, hi, wins, n, p = stats
                records[-1]['paired_stats'] = {'ratio': ratio, 'ci_low': lo, 'ci_high': hi,
                                         'b_faster': wins, 'pairs': n, 'sign_test_p': p}
                print(f'{"":28} paired B/A {ratio:.3f} (95% CI {lo:.3f}-{hi:.3f}), '
                      f'B faster in {wins} of {n} pairs, sign test p={p:.4f}', flush=True)
    print(f'{"TOTAL (sum of medians)":28} {total_a*1000:7.1f}ms {total_b*1000:7.1f}ms {total_b/total_a:6.2f}')
    if args.json:
        with open(args.json, 'w') as f:
            json.dump({'reps': args.reps, 'db': db_key, 'baseline_sha': baseline_sha,
                       'candidate_sha': file_sha(os.path.join(args.b, 'osm3s_query')),
                       'queries': records}, f, indent=1)
    if not args.no_cache:
        os.makedirs(os.path.dirname(args.cache), exist_ok=True)
        with open(args.cache, 'w') as f:
            json.dump(cache, f)
        print(f'baseline: {reused} of {len(args.queries)} queries reused from {args.cache}; '
              f'A and B were timed separately, not alternately')
    if differing:
        print('Outputs differ for: ' + ', '.join(differing))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
