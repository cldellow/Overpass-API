#!/usr/bin/env python3
"""Check that a selected corpus reproduces: re-runs of it (coverage.py with jsonl:SELECTION) must
give every query the status and output digest recorded in the selection, and, for runs on the
instrumented build, cover the same features as the pool did.

  verify.py SELECTION --pool POOL.jsonl --cov RUN.jsonl [--cov RUN2.jsonl ...] [--plain RUN.jsonl ...]

--cov runs are on the instrumented build: their features are compared with the selection's, as
recorded in the pool. --plain runs are on an uninstrumented build: only results are compared.
--edgemap and --exclude-prefix as for select.py."""
import argparse
import json
import sys


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('selection')
    ap.add_argument('--pool', required=True, help='the (stable) pool the selection was made from')
    ap.add_argument('--cov', action='append', default=[])
    ap.add_argument('--plain', action='append', default=[])
    ap.add_argument('--edgemap')
    ap.add_argument('--exclude-prefix', action='append', default=[])
    args = ap.parse_args()

    keep = None
    if args.exclude_prefix:
        files = json.load(open(args.edgemap))['files']
        keep = [not any(f.startswith(p) for p in args.exclude_prefix) for f in files]

    def feats(fs):
        return {f for f in fs if keep is None or keep[f >> 4]}

    sel = {}
    for line in open(args.selection):
        r = json.loads(line)
        sel[r['id']] = (r['status'], r['digest'])
    expected = set()
    for line in open(args.pool):
        r = json.loads(line)
        if r['id'] in sel and r['features']:
            expected |= feats(r['features'])

    ok = True
    for kind, paths in (('cov', args.cov), ('plain', args.plain)):
        for path in paths:
            got, union = {}, set()
            for line in open(path):
                r = json.loads(line)
                got[r['id']] = (r['status'], r['digest'])
                if kind == 'cov' and r['features']:
                    union |= feats(r['features'])
            missing = set(sel) - set(got)
            differ = sorted(q for q in sel if q in got and got[q] != sel[q])
            msg = f'{path}: {len(got)} queries, {len(missing)} missing, {len(differ)} with another result'
            if kind == 'cov':
                lost, extra = expected - union, union - expected
                msg += (f'; features: {len(union)} covered, {len(expected)} expected, {len(lost)} lost, '
                        f'{len(extra)} new; edges lost: {len({f >> 4 for f in lost} - {f >> 4 for f in union})}')
                ok &= not lost
            ok &= not missing and not differ
            print(msg)
            for q in differ[:10]:
                print(f'  {q}: expected {sel[q][0]}, got {got[q][0]}')
    print('reproduces' if ok else 'DOES NOT REPRODUCE')
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
