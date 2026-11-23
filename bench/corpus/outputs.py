#!/usr/bin/env python3
"""How much does each corpus query actually produce? Runs a corpus on a build and counts the
elements in each query's output, so a corpus that only exercises code with empty sets shows up.

  outputs.py CORPUS.jsonl --bin BINDIR --db DB [--jobs N] [--out per-query.jsonl]

Counts, per query, output elements across formats: XML/OSM elements (<node, <way, <relation,
<area, <count), JSON elements ("type": ...), CSV rows and popup entries. Prints the distribution
(errors, empty, 1, 2-10, 11-100, >100 elements) overall and by origin. Writes no output text,
only counts."""
import argparse
import collections
import json
import multiprocessing
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import fuzz  # noqa: E402

XML_EL = re.compile(rb'<(?:node|way|relation|area|count|tag-only|nd|member)\b')
XML_TOP = re.compile(rb'<(?:node|way|relation|area|count)\b')
JSON_EL = re.compile(rb'"type":\s*"(?:node|way|relation|area|count|[^"]+)"')
POPUP = re.compile(rb'<(?:strong|h2)\b')


def count_elements(out, query):
    head = query.split(';', 1)[0]
    if '[out:csv' in head:
        lines = [x for x in out.split(b'\n') if x.strip()]
        # a header line unless the header option is false
        has_header = not re.search(r'\[out:csv\([^)]*;\s*false', head)
        return max(0, len(lines) - (1 if has_header else 0))
    if '[out:json' in head:
        return len(JSON_EL.findall(out))
    if '[out:popup' in head:
        return len(POPUP.findall(out))
    return len(XML_TOP.findall(out))


def work(task):
    rec, bindir, db, timeout = task
    status, _digest, out, _ = fuzz.run_once(bindir, db, rec['query'], timeout)
    n = count_elements(out or b'', rec['query']) if status == 'ok' else None
    return {'id': rec['id'], 'status': status, 'elements': n, 'bytes': len(out or b'')}


def bucket(r):
    if r['status'] != 'ok':
        return 'error/limit'
    n = r['elements']
    return 'empty' if n == 0 else '1' if n == 1 else '2-10' if n <= 10 else '11-100' if n <= 100 else '>100'


def origin(qid):
    if qid.startswith('fuzz:'):
        feats = qid.split(':')[2]
        return 'history' if 'history' in feats else ('areas' if 'areas' in feats else 'plain')
    return qid.split(':')[0]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('corpus')
    ap.add_argument('--bin', required=True)
    ap.add_argument('--db', required=True)
    ap.add_argument('--jobs', type=int, default=os.cpu_count())
    ap.add_argument('--timeout', type=int, default=300)
    ap.add_argument('--out')
    args = ap.parse_args()
    recs = [json.loads(line) for line in open(args.corpus)]
    with multiprocessing.Pool(args.jobs) as pool:
        res = pool.map(work, [(r, args.bin, args.db, args.timeout) for r in recs], chunksize=4)
    if args.out:
        with open(args.out, 'w') as f:
            for r in res:
                f.write(json.dumps(r) + '\n')
    order = ['error/limit', 'empty', '1', '2-10', '11-100', '>100']
    total = collections.Counter(bucket(r) for r in res)
    print(f'{len(res)} queries: ' + ', '.join(f'{k} {total[k]} ({100 * total[k] / len(res):.0f}%)' for k in order))
    by = collections.defaultdict(collections.Counter)
    for r in res:
        by[origin(r['id'])][bucket(r)] += 1
    for o in sorted(by):
        c = by[o]
        n = sum(c.values())
        print(f'  {o:14s} {n:5d}: ' + ', '.join(f'{k} {100 * c[k] / n:.0f}%' for k in order))


if __name__ == '__main__':
    main()
