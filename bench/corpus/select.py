#!/usr/bin/env python3
"""Pick a small set of queries that covers the features of a coverage.py run.

Lazy greedy weighted set cover: repeatedly take the query with the most not-yet-covered features
per second of CPU (plus --overhead per query), until every feature seen is covered. Queries that
timed out or hit a resource limit have no features and are skipped.

Prints the coverage curve (how many queries reach 90/95/99/100% of the features, and of the
edges ignoring hit counts) and writes the selection, in pick order, as JSON lines."""
import argparse
import heapq
import json
import sys


def load(paths, keep_edge=None):
    recs = []
    for p in paths:
        for line in open(p):
            r = json.loads(line)
            if r.get('features'):
                feats = r['features'] if keep_edge is None else [f for f in r['features'] if keep_edge[f >> 4]]
                if feats:
                    r['features'] = set(feats)
                    recs.append(r)
    return recs


def greedy(recs, overhead):
    covered = set()
    heap = [(-len(r['features']) / (r['cpu'] + overhead), i) for i, r in enumerate(recs)]
    heapq.heapify(heap)
    picks = []
    while heap:
        neg, i = heapq.heappop(heap)
        gain = len(recs[i]['features'] - covered)
        if gain == 0:
            continue
        score = gain / (recs[i]['cpu'] + overhead)
        if heap and score < -heap[0][0] - 1e-12:
            heapq.heappush(heap, (-score, i))
            continue
        covered |= recs[i]['features']
        picks.append((i, gain))
    return picks


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('inputs', nargs='+', help='coverage.py JSON lines')
    ap.add_argument('--overhead', type=float, default=0.05, help='seconds added to each query\'s CPU')
    ap.add_argument('--out', help='write the selection here')
    ap.add_argument('--edgemap', help='edge to source file map (JSON with "files"), for --exclude-prefix')
    ap.add_argument('--exclude-prefix', action='append', default=[],
                    help='ignore edges in source files with this prefix, e.g. /usr for the C++ library '
                         '(repeatable; needs --edgemap)')
    args = ap.parse_args()

    keep_edge = None
    if args.exclude_prefix:
        if not args.edgemap:
            sys.exit('--exclude-prefix needs --edgemap')
        files = json.load(open(args.edgemap))['files']
        keep_edge = [not any(f.startswith(p) for p in args.exclude_prefix) for f in files]
    recs = load(args.inputs, keep_edge)
    universe = set().union(*(r['features'] for r in recs))
    edges = {f >> 4 for f in universe}
    picks = greedy(recs, args.overhead)

    print(f'{len(recs)} queries with coverage, {len(universe)} features on {len(edges)} edges')
    covered, cov_edges, cpu = set(), set(), 0.0
    marks = [0.90, 0.95, 0.99, 1.0]
    fmarks, emarks = {}, {}
    for n, (i, _) in enumerate(picks, 1):
        covered |= recs[i]['features']
        cov_edges |= {f >> 4 for f in recs[i]['features']}
        cpu += recs[i]['cpu']
        for m in marks:
            if m not in fmarks and len(covered) >= m * len(universe):
                fmarks[m] = (n, cpu)
            if m not in emarks and len(cov_edges) >= m * len(edges):
                emarks[m] = (n, cpu)
    print('share   features: queries (cpu)   edges: queries (cpu)')
    for m in marks:
        f, e = fmarks.get(m), emarks.get(m)
        print(f'{m:5.0%}   {f[0]:6d} ({f[1]:7.1f} s)       {e[0]:6d} ({e[1]:7.1f} s)')
    total_cpu = sum(r['cpu'] for r in recs)
    print(f'all queries: {total_cpu:.0f} s cpu; selection: {len(picks)} queries, {cpu:.1f} s cpu')
    kinds = {}
    for i, _ in picks:
        k = recs[i]['id'].split(':')[0] + (':' + recs[i]['id'].split(':')[2] if recs[i]['id'].startswith('fuzz') else '')
        kinds[k] = kinds.get(k, 0) + 1
    print('selection by source:', kinds)
    if args.out:
        with open(args.out, 'w') as out:
            for i, gain in picks:
                r = recs[i]
                out.write(json.dumps({'id': r['id'], 'gain': gain, 'cpu': r['cpu'], 'status': r['status'],
                                      'digest': r['digest'], 'query': r['query']}) + '\n')


if __name__ == '__main__':
    main()
