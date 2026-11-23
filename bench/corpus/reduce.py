#!/usr/bin/env python3
"""Shrink the queries of a corpus while keeping what each contributes to its coverage.

Each covered edge is owned by the first query in the corpus's order (select.py's pick order) that
reaches it, in every one of two baseline runs. A reduced query must still reach the edges it owns,
so the corpus as a whole keeps its coverage, and must keep its result class: the same status, some
output if it had output before, an `out` statement if it had one, and at most --max-cpu seconds.
Hit counts are not kept.

Two more rules keep queries meaningful: a function's argument list is never deleted (s.max(...)
would still parse, as the tag max), and a set the original defines must stay defined while the
query still uses it (otherwise its statements run on an empty set).

Reduction is greedy and structure-aware: it tries to delete whole statements (also inside unions
and blocks), settings and filters in [...] or (...), items of comma lists (CSV fields, convert
pairs), and `out` options, the biggest first, and keeps any deletion that preserves the above,
until none does or the budget (--max-evals, --time-limit per query) runs out.

  reduce.py own CORPUS.jsonl --bin DIR --db DB --out OWNERS.json [--edgemap MAP --exclude-prefix /usr]
  reduce.py reduce CORPUS.jsonl --owners OWNERS.json --bin DIR --db DB --out REDUCED.jsonl
                   [--shard K --shards N] [--jobs J]
  reduce.py check REDUCED.jsonl --owners OWNERS.json --bin DIR --db DB --out CHECK.json

The binary is the coverage build of coverage.py (SanitizerCoverage counters, cov_rt.c); each
evaluation runs the query and the parser's three dump modes, as coverage.py --dumps does."""
import argparse
import importlib.util
import json
import multiprocessing
import os
import re
import resource
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
import fuzz  # noqa: E402


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


covmod = _load('corpus_coverage', os.path.join(HERE, 'coverage.py'))
outmod = _load('corpus_outputs', os.path.join(HERE, 'outputs.py'))

OUT_RE = re.compile(r'\bout\b')
# Words that may directly precede a removable filter in parentheses: node(bbox), nwr.s1(...).
QUERY_TYPES = {'node', 'way', 'rel', 'relation', 'nwr', 'nw', 'nr', 'wr', 'area', 'derived'}
WORD_BEFORE = re.compile(r'([A-Za-z_]\w*)\s*$')
DEFINED = re.compile(r'->\s*\.([A-Za-z_]\w*)')


def is_call_args(q, o):
    """Whether the "(" at o opens a function's argument list (max(...), count_by_role(...)), which
    is never deleted: without it, s.max(...) still parses, as the value of the tag max."""
    m = WORD_BEFORE.search(q[:o])
    if not m:
        return False
    word, before = m.group(1), q[:m.start()].rstrip()
    if word in QUERY_TYPES:
        return False
    if before.endswith('.'):
        # node.s1(...) filters set s1; s2.max(...) calls max on s2
        prev = WORD_BEFORE.search(before[:-1])
        return not (prev and prev.group(1) in QUERY_TYPES)
    return True


def undefined_sets(q, defined_before):
    """Sets the original query defined that q still uses but no longer defines."""
    defined = set(DEFINED.findall(q))
    missing = set()
    for name in defined_before - defined:
        if re.search(r'(?<![\w])' + re.escape(name) + r'(?![\w])', q):
            missing.add(name)
    return missing


# ----- evaluation --------------------------------------------------------------------------------

def child_cpu():
    u = resource.getrusage(resource.RUSAGE_CHILDREN)
    return u.ru_utime + u.ru_stime


def evaluate(bindir, db, query, timeout, tmpdir):
    """status, elements (None unless ok), edges (set) and CPU seconds of one query."""
    cov = os.path.join(tmpdir, f'{os.getpid()}.cov')
    if os.path.exists(cov):
        os.unlink(cov)
    os.environ['COV_OUT'] = cov
    before = child_cpu()
    status, _digest, out, _ = fuzz.run_once(bindir, db, query, timeout)
    cpu = child_cpu() - before
    if status in fuzz.UNCACHEABLE:
        return status, None, set(), cpu
    feats = set(covmod.read_features(cov) or [])
    feats |= covmod.dump_features(bindir, query, cov, timeout)
    elements = outmod.count_elements(out or b'', query) if status == 'ok' else None
    return status, elements, {f >> 4 for f in feats}, cpu


# ----- candidates --------------------------------------------------------------------------------

PAIRS = {'(': ')', '[': ']', '{': '}'}


def scan(q):
    """Bracket groups as (open, close, kind, parent) and, per container (-1 for the whole text,
    else a group index), the positions of statement boundaries and of depth-1 commas."""
    groups, stack = [], []
    bounds, commas = {-1: []}, {-1: []}
    i, n = 0, len(q)
    while i < n:
        c = q[i]
        if c in '"\'':
            j = i + 1
            while j < n and q[j] != c:
                j += 2 if q[j] == '\\' else 1
            i = j + 1
            continue
        parent = stack[-1] if stack else -1
        if c in PAIRS:
            groups.append([i, None, c, parent])
            g = len(groups) - 1
            bounds[g], commas[g] = [], []
            stack.append(g)
        elif c in ')]}' and stack:
            g = stack.pop()
            groups[g][1] = i
            if c == '}':
                # a block ends a statement unless "else" or ";" follows
                rest = q[i + 1:].lstrip()
                if not rest.startswith(('else', ';')):
                    bounds[groups[g][3]].append(i)
        elif c == ';':
            bounds[parent].append(i)
        elif c == ',':
            commas[parent].append(i)
        i += 1
    return [tuple(g) for g in groups], bounds, commas


def candidates(q):
    groups, bounds, commas = scan(q)
    cands = set()
    # statements, in every container
    for cont, bs in bounds.items():
        if cont != -1 and groups[cont][1] is None:
            continue
        prev = 0 if cont == -1 else groups[cont][0] + 1
        for b in sorted(bs):
            if q[prev:b + 1].strip():
                cands.add((prev, b + 1))
            prev = b + 1
    for g, (o, c, kind, parent) in enumerate(groups):
        if c is None:
            continue
        before = q[:o].rstrip()
        at_start = not before or before[-1] in ';{}('
        if kind == '[' or (kind == '(' and not at_start and not is_call_args(q, o)):
            cands.add((o, c + 1))
        # comma-list items inside this group
        cs = sorted(commas.get(g, []))
        if cs:
            edges = [o] + cs + [c]
            for k in range(len(edges) - 1):
                a, b = edges[k], edges[k + 1]
                cands.add((a + 1, b + 1) if k == 0 else (a, b))
    # out options
    for m in re.finditer(r'\bout\b([^;]*);', q):
        for t in re.finditer(r'\S+', m.group(1)):
            if not t.group().startswith(('(', '[')):
                cands.add((m.start(1) + t.start(), m.start(1) + t.end()))
    texts = []
    seen = {q}
    for a, b in sorted(cands, key=lambda ab: ab[0] - ab[1]):
        t = q[:a] + q[b:]
        t = re.sub(r'\n\s*\n', '\n', t)
        if t not in seen and t.strip():
            seen.add(t)
            texts.append(t)
    return texts


# ----- reduction ---------------------------------------------------------------------------------

def reduce_one(task):
    i, rec, owned, base, bindir, db, args, tmpdir = task
    status0, elements0 = base
    q0 = rec['query']
    need_out = bool(OUT_RE.search(q0))
    defined0 = set(DEFINED.findall(q0))

    def acceptable(q):
        if need_out and not OUT_RE.search(q):
            return False
        if undefined_sets(q, defined0):
            return False  # it would work on an empty set where the original had a real one
        st, el, edges, cpu = evaluate(bindir, db, q, args.eval_timeout, tmpdir)
        if st != status0 or cpu > args.max_cpu:
            return False
        if elements0 and not el:
            return False
        return owned <= edges

    cur, evals, t0 = q0, 0, time.time()
    improved = True
    while improved and evals < args.max_evals and time.time() - t0 < args.time_limit:
        improved = False
        for cand in candidates(cur):
            if evals >= args.max_evals or time.time() - t0 >= args.time_limit:
                break
            evals += 1
            if acceptable(cand):
                cur, improved = cand, True
                break
    tidy = re.sub(r'[ \t]+;', ';', re.sub(r'[ \t]+\n', '\n', cur)).strip() + '\n'
    if tidy != cur:
        evals += 1
        if acceptable(tidy):
            cur = tidy
    return {'i': i, 'id': rec['id'], 'query': cur, 'orig_len': len(q0), 'len': len(cur), 'evals': evals,
            'seconds': round(time.time() - t0, 1), 'owned': len(owned)}


def cmd_own(args):
    recs = [json.loads(line) for line in open(args.corpus)]
    keep = None
    if args.exclude_prefix:
        files = json.load(open(args.edgemap))['files']
        keep = [not any(f.startswith(p) for p in args.exclude_prefix) for f in files]
    tmp = tempfile.mkdtemp(prefix='reduce-own-')
    runs = []
    for _ in range(2):
        with multiprocessing.Pool(args.jobs) as pool:
            runs.append(pool.starmap(evaluate, [(args.bin, args.db, r['query'], args.eval_timeout, tmp) for r in recs]))
    owners, seen, base, unstable = {}, set(), {}, 0
    for i in range(len(recs)):
        (s1, e1, d1, _), (s2, e2, d2, _) = runs[0][i], runs[1][i]
        if (s1, e1) != (s2, e2):
            unstable += 1
        edges = d1 & d2
        if keep is not None:
            edges = {e for e in edges if e < len(keep) and keep[e]}
        mine = edges - seen
        seen |= edges
        owners[str(i)] = sorted(mine)
        base[str(i)] = [s1, e1]
    json.dump({'owners': owners, 'base': base}, open(args.out, 'w'))
    print(f'{len(recs)} queries, {len(seen)} edges owned, {unstable} with results that differ between the two runs')


def cmd_reduce(args):
    recs = [json.loads(line) for line in open(args.corpus)]
    own = json.load(open(args.owners))
    tmp = tempfile.mkdtemp(prefix='reduce-')
    mine = [i for i in range(len(recs)) if i % args.shards == args.shard
            and (own['owners'][str(i)] or not args.skip_unowned)]
    tasks = [(i, recs[i], set(own['owners'][str(i)]), tuple(own['base'][str(i)]), args.bin, args.db, args, tmp)
             for i in mine]
    done = []
    with multiprocessing.Pool(args.jobs) as pool, open(args.out, 'w') as out:
        for r in pool.imap_unordered(reduce_one, tasks):
            out.write(json.dumps(r) + '\n')
            out.flush()
            done.append(r)
            if len(done) % 20 == 0:
                print(f'{len(done)}/{len(tasks)}', flush=True)
    o = sum(r['orig_len'] for r in done)
    n = sum(r['len'] for r in done)
    print(f'{len(done)} queries reduced: {o} -> {n} characters ({100 * (1 - n / max(o, 1)):.0f}% smaller), '
          f'{sum(r["evals"] for r in done)} evaluations')


def cmd_check(args):
    """Run the reduced corpus once: does it still reach every owned edge, and keep each query's
    result class? Prints counts only."""
    recs = [json.loads(line) for line in open(args.corpus)]
    own = json.load(open(args.owners))
    tmp = tempfile.mkdtemp(prefix='reduce-check-')
    with multiprocessing.Pool(args.jobs) as pool:
        res = pool.starmap(evaluate, [(args.bin, args.db, r['query'], args.eval_timeout, tmp) for r in recs])
    reached = set().union(*(edges for _, _, edges, _ in res))
    owned = set().union(*(set(v) for v in own['owners'].values()))
    changed = sum(1 for k, (st, el, _, _) in enumerate(res)
                  if st != own['base'][str(k)][0] or (own['base'][str(k)][1] and not el))
    lost = owned - reached
    with open(args.out, 'w') as f:
        json.dump({'queries': len(recs), 'owned_edges': len(owned), 'lost_edges': len(lost),
                   'result_class_changed': changed}, f)
    print(f'{len(recs)} reduced queries: {len(owned) - len(lost)} of {len(owned)} owned edges reached '
          f'({len(lost)} lost); {changed} queries changed result class')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    for name in ('own', 'reduce', 'check'):
        p = sub.add_parser(name)
        p.add_argument('corpus')
        p.add_argument('--bin', required=True)
        p.add_argument('--db', required=True)
        p.add_argument('--out', required=True)
        p.add_argument('--jobs', type=int, default=os.cpu_count())
        p.add_argument('--eval-timeout', type=int, default=20)
    p = sub.choices['own']
    p.add_argument('--edgemap')
    p.add_argument('--exclude-prefix', action='append', default=[])
    sub.choices['check'].add_argument('--owners', required=True)
    p = sub.choices['reduce']
    p.add_argument('--owners', required=True)
    p.add_argument('--shard', type=int, default=0)
    p.add_argument('--shards', type=int, default=1)
    p.add_argument('--max-evals', type=int, default=300)
    p.add_argument('--time-limit', type=float, default=300)
    p.add_argument('--max-cpu', type=float, default=5.0)
    p.add_argument('--skip-unowned', action='store_true',
                   help='leave out queries that own no edge (they would only keep hit counts)')
    args = ap.parse_args()
    {'own': cmd_own, 'reduce': cmd_reduce, 'check': cmd_check}[args.cmd](args)


if __name__ == '__main__':
    main()
