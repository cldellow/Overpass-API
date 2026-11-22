#!/usr/bin/env python3
"""Find queries that overrun their [timeout:] by a wide margin: loops that don't check the
query's time limit often enough.

Runs fuzz.py's queries with [timeout:T] in place of their own limit and reports those whose
wall time exceeds T by more than --slack seconds, with the statement where osm3s_query
finally noticed the limit (from its "Query timed out" error), or "killed" if it was still
running after --kill seconds.

  deadline_audit.py --bin BUILD/bin --db DB --vocab V.json --seed 7 --count 2000 --limit 2

Prints query indices, not query text, so it can be used with private vocabularies; use
fuzz.py --show to see a query.
"""
import argparse
import collections
import importlib.util
import os
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('fuzz', os.path.join(HERE, 'fuzz.py'))
fuzz = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fuzz)


def run(args, vocab, features, index):
    query = fuzz.generate(vocab, args.seed, index, features)
    query = re.sub(r'\[timeout:\d+\]', f'[timeout:{args.limit}]', query, count=1)
    start = time.monotonic()
    try:
        p = subprocess.run([os.path.join(args.bin, 'osm3s_query'), '--db-dir=' + args.db],
                           input=query.encode(), capture_output=True, timeout=args.kill)
        out = (p.stdout[-4000:] + p.stderr).decode(errors='replace')
        m = re.search(r'Query timed out in "([^"]+)" at line (\d+)', out)
        where = f'{m.group(1)} (line {m.group(2)})' if m else ('timed out' if 'timed out' in out else 'finished')
    except subprocess.TimeoutExpired:
        where = 'killed'
    return index, time.monotonic() - start, where


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--bin', required=True)
    parser.add_argument('--db', required=True)
    parser.add_argument('--vocab', required=True)
    parser.add_argument('--seed', type=int, default=7)
    parser.add_argument('--start', type=int, default=0)
    parser.add_argument('--count', type=int, default=1000)
    parser.add_argument('--jobs', type=int, default=2)
    parser.add_argument('--limit', type=int, default=2, help='[timeout:] given to every query, seconds')
    parser.add_argument('--slack', type=float, default=5, help='overrun reported, seconds')
    parser.add_argument('--kill', type=int, default=120, help='seconds before a run is killed')
    parser.add_argument('--features', default='')
    args = parser.parse_args()
    import json
    vocab = json.load(open(args.vocab))
    features = frozenset(f for f in args.features.split(',') if f)

    with ThreadPoolExecutor(args.jobs) as pool:
        results = list(pool.map(lambda i: run(args, vocab, features, i),
                                range(args.start, args.start + args.count)))
    hit = [r for r in results if r[2] != 'finished']
    late = sorted((r for r in hit if r[1] > args.limit + args.slack), key=lambda r: -r[1])
    print(f'{len(results)} queries, {len(hit)} reached the {args.limit} s limit, '
          f'{len(late)} overran it by more than {args.slack:g} s')
    by_where = collections.Counter(r[2] for r in late)
    for where, n in by_where.most_common():
        print(f'  {n:4d}  noticed in {where}')
    for index, wall, where in late[:30]:
        print(f'  query {index}: {wall:.1f} s, {where}')


if __name__ == '__main__':
    main()
