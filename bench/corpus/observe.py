#!/usr/bin/env python3
"""Make every named set of each query observable: append `.X out count; .X out ids 1000;` for each
set X the query assigns (`->.X`), in order of first assignment, so that a statement computing a
different result changes the query's output even when the query itself never printed that set.

  observe.py IN.jsonl OUT.jsonl --bin BINDIR --db DB [--jobs N] [--max-extra 2]

Printing a set can be expensive: under [adiff:], or for a set holding most of the database. So each
query is run as given and with the additions. If the additions change its status (e.g. it now runs
out of memory) or cost more than --max-extra seconds, each set is tried on its own, and only the
sets whose additions keep the status and cost at most --max-extra seconds are kept. Keeps each
record's id; drops fields that describe the old output (status, digest, cpu)."""
import argparse
import json
import multiprocessing
import os
import re
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import fuzz  # noqa: E402

ASSIGN = re.compile(r'->\s*\.([A-Za-z_][A-Za-z0-9_]*)')


def with_sets(query, names):
    if not names:
        return query
    return query.rstrip() + '\n' + ''.join(f'.{n} out count;\n.{n} out ids 1000;\n' for n in names)


def run(bindir, db, query, timeout):
    t = time.monotonic()
    status = fuzz.run_once(bindir, db, query, timeout)[0]
    return status, time.monotonic() - t


def work(task):
    rec, bindir, db, max_extra, timeout = task
    query = rec['query']
    names = list(dict.fromkeys(ASSIGN.findall(query)))
    if not names:
        return rec['id'], query, 0, 0
    status0, t0 = run(bindir, db, query, timeout)
    status, t = run(bindir, db, with_sets(query, names), timeout)
    if status == status0 and t <= t0 + max_extra:
        return rec['id'], with_sets(query, names), len(names), 0
    keep = []
    for n in names:
        status, t = run(bindir, db, with_sets(query, [n]), timeout)
        if status == status0 and t <= t0 + max_extra:
            keep.append(n)
    return rec['id'], with_sets(query, keep), len(keep), len(names) - len(keep)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('src')
    ap.add_argument('dst')
    ap.add_argument('--bin', required=True)
    ap.add_argument('--db', required=True)
    ap.add_argument('--jobs', type=int, default=os.cpu_count())
    ap.add_argument('--max-extra', type=float, default=2.0, help='seconds the additions may add')
    ap.add_argument('--timeout', type=int, default=300)
    args = ap.parse_args()
    recs = [json.loads(line) for line in open(args.src)]
    with multiprocessing.Pool(args.jobs) as pool:
        res = pool.map(work, [(r, args.bin, args.db, args.max_extra, args.timeout) for r in recs], chunksize=1)
    with open(args.dst, 'w') as out:
        for qid, query, _, _ in res:
            out.write(json.dumps({'id': qid, 'query': query}) + '\n')
    print(f'{len(res)} queries; {sum(r[2] for r in res)} sets made visible; '
          f'{sum(r[3] for r in res)} left out (too expensive to print) in {sum(r[3] > 0 for r in res)} queries')


if __name__ == '__main__':
    main()
