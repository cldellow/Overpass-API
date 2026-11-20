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

Usage:
  compare.py --a OLD/bin --b NEW/bin --db DB_DIR [--reps 7] [--no-cache] QUERY.ql [QUERY.ql ...]

Exit status is 1 if any query's output differs between the builds.
"""
import argparse
import hashlib
import json
import os
import re
import resource
import statistics
import subprocess
import sys

# Lines that name the build, so they differ between builds without the data differing.
VOLATILE = re.compile(rb'(generator|Overpass API \d|"version":)')


def run_query(bindir, db, query_path):
    """Run one query; return (cpu_seconds, output_digest, output_bytes)."""
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    with open(query_path, 'rb') as stdin:
        proc = subprocess.run(
            [os.path.join(bindir, 'osm3s_query'), '--db-dir=' + db.rstrip('/') + '/'],
            stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    cpu = (after.ru_utime - before.ru_utime) + (after.ru_stime - before.ru_stime)
    if proc.returncode != 0:
        sys.exit(f'{query_path}: osm3s_query in {bindir} exited with {proc.returncode}')
    stable = b'\n'.join(line for line in proc.stdout.split(b'\n') if not VOLATILE.search(line))
    return cpu, hashlib.sha256(stable).hexdigest(), len(proc.stdout)


def file_sha(path):
    with open(path, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


def measure(bindir, db, query, reps):
    """Run a query reps times after one warm-up; return times, digests and output size."""
    run_query(bindir, db, query)
    times, digests, size = [], set(), 0
    for _ in range(reps):
        seconds, digest, size = run_query(bindir, db, query)
        times.append(seconds)
        digests.add(digest)
    return {'times': times, 'digests': sorted(digests), 'size': size}


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
    parser.add_argument('queries', nargs='+')
    args = parser.parse_args()

    cache = {}
    if not args.no_cache and os.path.exists(args.cache):
        with open(args.cache) as f:
            cache = json.load(f)
    baseline_sha = file_sha(os.path.join(args.a, 'osm3s_query'))
    db_key = os.path.realpath(args.db)

    print(f'{"query":28} {"A cpu":>9} {"B cpu":>9} {"B/A":>6}  {"A spread":>15} {"B spread":>15}  output')
    total_a = total_b = 0.0
    differing = []
    reused = 0
    for query in args.queries:
        key = f'{baseline_sha}|{db_key}|{file_sha(query)}'
        if args.no_cache:
            run_query(args.a, args.db, query)
            run_query(args.b, args.db, query)
            a = {'times': [], 'digests': set(), 'size': 0}
            b = {'times': [], 'digests': set(), 'size': 0}
            for _ in range(args.reps):
                for result, bindir in ((a, args.a), (b, args.b)):
                    seconds, digest, size = run_query(bindir, args.db, query)
                    result['times'].append(seconds)
                    result['digests'].add(digest)
                    result['size'] = size
            a['digests'] = sorted(a['digests'])
            b['digests'] = sorted(b['digests'])
        else:
            if key in cache:
                a = cache[key]
                reused += 1
            else:
                a = measure(args.a, args.db, query, args.reps)
                cache[key] = a
            b = measure(args.b, args.db, query, args.reps)
        med_a, med_b = statistics.median(a['times']), statistics.median(b['times'])
        total_a += med_a
        total_b += med_b
        same = a['digests'] == b['digests'] and len(a['digests']) == 1
        if not same:
            differing.append(query)
        name = os.path.splitext(os.path.basename(query))[0]
        spread = lambda xs: f'{min(xs)*1000:6.1f}-{max(xs)*1000:6.1f}ms'
        print(f'{name:28} {med_a*1000:7.1f}ms {med_b*1000:7.1f}ms {med_b/med_a:6.2f}  '
              f'{spread(a["times"]):>15} {spread(b["times"]):>15}  '
              f'{"same" if same else "DIFFERENT"} ({b["size"]} bytes)', flush=True)
    print(f'{"TOTAL (sum of medians)":28} {total_a*1000:7.1f}ms {total_b*1000:7.1f}ms {total_b/total_a:6.2f}')
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
