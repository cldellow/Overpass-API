#!/usr/bin/env python3
"""Compare two Overpass builds on the same database: CPU time per query and output equality.

Each query runs --reps times per build, alternating A and B so that background
noise affects both builds alike. CPU time is user+system time of the
osm3s_query process (from getrusage), which ignores time spent waiting.

Usage:
  compare.py --a OLD/bin --b NEW/bin --db DB_DIR [--reps 7] QUERY.ql [QUERY.ql ...]

Exit status is 1 if any query's output differs between the builds.
"""
import argparse
import hashlib
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


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--a', required=True, help='bin directory of the baseline build')
    parser.add_argument('--b', required=True, help='bin directory of the build under test')
    parser.add_argument('--db', required=True, help='database directory')
    parser.add_argument('--reps', type=int, default=7, help='runs per query per build (default 7)')
    parser.add_argument('queries', nargs='+')
    args = parser.parse_args()

    print(f'{"query":28} {"A cpu":>9} {"B cpu":>9} {"B/A":>6}  {"A spread":>15} {"B spread":>15}  output')
    total_a = total_b = 0.0
    differing = []
    for query in args.queries:
        # One untimed warm-up run per build, so both start with the same page cache.
        run_query(args.a, args.db, query)
        run_query(args.b, args.db, query)
        cpu = {'a': [], 'b': []}
        digests = {'a': set(), 'b': set()}
        size = 0
        for _ in range(args.reps):
            for key, bindir in (('a', args.a), ('b', args.b)):
                seconds, digest, size = run_query(bindir, args.db, query)
                cpu[key].append(seconds)
                digests[key].add(digest)
        med_a, med_b = statistics.median(cpu['a']), statistics.median(cpu['b'])
        total_a += med_a
        total_b += med_b
        same = digests['a'] == digests['b'] and len(digests['a']) == 1
        if not same:
            differing.append(query)
        name = os.path.splitext(os.path.basename(query))[0]
        spread = lambda xs: f'{min(xs)*1000:6.1f}-{max(xs)*1000:6.1f}ms'
        print(f'{name:28} {med_a*1000:7.1f}ms {med_b*1000:7.1f}ms {med_b/med_a:6.2f}  '
              f'{spread(cpu["a"]):>15} {spread(cpu["b"]):>15}  {"same" if same else "DIFFERENT"} ({size} bytes)')
    print(f'{"TOTAL (sum of medians)":28} {total_a*1000:7.1f}ms {total_b*1000:7.1f}ms {total_b/total_a:6.2f}')
    if differing:
        print('Outputs differ for: ' + ', '.join(differing))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
