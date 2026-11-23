#!/usr/bin/env python3
"""Keep only what is reproducible across repeated coverage.py runs of the same pool.

For each query id present in every run: if its status or output digest differs between runs, the
query is dropped (its result is not deterministic, so it can't be a conformance test). Otherwise
its features are the intersection over the runs, so a feature only counts if every run saw it.

Writes the merged records (as coverage.py's, with the run-to-run CPU maximum) to --out, and prints
how many queries and features were dropped as unstable."""
import argparse
import json
import sys
from array import array


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('runs', nargs='+', help='coverage.py JSON lines, one file per run of the same pool')
    ap.add_argument('--out', required=True)
    args = ap.parse_args()
    if len(args.runs) < 2:
        sys.exit('need at least two runs')

    # The first run, kept compactly; later runs are streamed against it.
    base = {}
    for line in open(args.runs[0]):
        r = json.loads(line)
        feats = r.pop('features')
        r['features'] = array('I', feats) if feats is not None else None
        base[r['id']] = r
    seen = {qid: 1 for qid in base}
    unstable_result = set()
    feats_before = sum(len(r['features']) for r in base.values() if r['features'] is not None)
    for path in args.runs[1:]:
        for line in open(path):
            r = json.loads(line)
            b = base.get(r['id'])
            if b is None:
                continue
            seen[r['id']] += 1
            if (r['status'], r['digest']) != (b['status'], b['digest']):
                unstable_result.add(r['id'])
                continue
            b['cpu'] = max(b['cpu'], r['cpu'])
            if b['features'] is None or r['features'] is None:
                b['features'] = None
                continue
            keep = set(r['features'])
            b['features'] = array('I', (f for f in b['features'] if f in keep))

    missing = {qid for qid, n in seen.items() if n < len(args.runs)}
    kept = feats_after = 0
    all_after = set()
    with open(args.out, 'w') as out:
        for qid, r in base.items():
            if qid in missing or qid in unstable_result:
                continue
            feats = list(r['features']) if r['features'] is not None else None
            if feats is not None:
                feats_after += len(feats)
                all_after.update(feats)
            r['features'] = feats
            out.write(json.dumps(r, separators=(',', ':')) + '\n')
            kept += 1
    print(f'{len(base)} queries in the first run; dropped {len(missing)} missing from a later run and '
          f'{len(unstable_result)} with a status or digest that differs between runs; kept {kept}.')
    print(f'per-query features: {feats_before} in the first run, {feats_after} stable in every run '
          f'({feats_before - feats_after} dropped); distinct stable features: {len(all_after)}')
    if unstable_result:
        print('queries with unstable results:', ', '.join(sorted(unstable_result)[:50]))


if __name__ == '__main__':
    main()
