#!/usr/bin/env python3
"""Differential fuzzing: run random Overpass QL queries against two builds and report any difference.

Queries follow the Overpass QL grammar (https://github.com/b1tw153/overpass-ebnf) and draw tags,
ids, roles and coordinates from a vocabulary built by fuzz_vocab.py, so most of them return data.
They favour the paths that select objects by id: id filters, recursion, member and parent
filters, set operations, and loops.

Query N of seed S depends only on (S, N) and the vocabulary file, so any query can be regenerated
on its own, whatever --jobs is:

  fuzz.py --vocab V.json --seed 7 --show 1234            # print query 1234 of seed 7
  fuzz.py --a OLD/bin --b NEW/bin --db DB --vocab V.json --seed 7 --count 5000

Differences are written to --out-dir as <seed>-<index>/{query.ql,a.out,b.out}. Exit status is 1
if any query's output differs. Use the same build as --a and --b to check that the outputs are
deterministic before blaming a change.
"""
import argparse
import collections
import concurrent.futures
import hashlib
import json
import os
import random
import re
import subprocess
import sys

# Lines that name the build, so they differ between builds without the data differing.
VOLATILE = re.compile(rb'(generator|Overpass API \d|"version":)')
# Outcomes that depend on timing or memory, not on the code under test.
RESOURCE_LIMIT = re.compile(rb'(timed out|out of memory|Timeout is|Query run out of memory)', re.I)


class Generator:
    """Build one query from a seeded random source and the vocabulary."""

    def __init__(self, vocab, rng):
        self.v = vocab
        self.r = rng
        self.sets = []  # names of the sets defined so far, with their element types

    # ----- leaves -----------------------------------------------------------

    def pick(self, items):
        return items[self.r.randrange(len(items))]

    @staticmethod
    def quote(text):
        return '"' + text.replace('\\', '\\\\').replace('"', '\\"') + '"'

    def tag(self):
        return self.pick(self.v['rare_tags'] if self.r.random() < 0.4 else self.v['common_tags'])

    def tag_filter(self):
        key, value = self.tag()
        form = self.r.random()
        if form < 0.45:
            return f'[{self.quote(key)}={self.quote(value)}]'
        if form < 0.6:
            return f'[{self.quote(key)}]'
        if form < 0.68:
            return f'[!{self.quote(key)}]'
        if form < 0.76:
            return f'[{self.quote(key)}!={self.quote(value)}]'
        prefix = re.escape(value[:max(1, len(value) // 2)])
        if form < 0.88:
            return f'[{self.quote(key)}~{self.quote("^" + prefix)}{",i" if self.r.random() < 0.3 else ""}]'
        if form < 0.94:
            return f'[{self.quote(key)}!~{self.quote("^" + prefix)}]'
        return f'[~{self.quote("^" + re.escape(key[:3]))}~{self.quote(prefix)}]'

    def bbox(self, max_size=0.05):
        lat, lon = self.pick(self.v['node_coords'])
        h = self.r.uniform(0.001, max_size)
        w = self.r.uniform(0.001, max_size * 1.4)
        return f'({lat - h / 2:.6f},{lon - w / 2:.6f},{lat + h / 2:.6f},{lon + w / 2:.6f})'

    def around_point(self):
        lat, lon = self.pick(self.v['node_coords'])
        return f'(around:{self.r.choice([10, 50, 100, 250, 500, 1000])},{lat:.6f},{lon:.6f})'

    def ids(self, kind):
        pool = {'node': self.v['node_ids'], 'way': self.v['way_ids'], 'rel': self.v['relation_ids']}[kind]
        chosen = sorted({self.pick(pool) for _ in range(self.r.randint(1, 12))})
        if len(chosen) == 1 and self.r.random() < 0.5:
            return f'({chosen[0]})'
        return '(id:' + ','.join(map(str, chosen)) + ')'

    def evaluator(self):
        key, value = self.tag()
        return self.r.choice([
            f't[{self.quote(key)}]=={self.quote(value)}',
            f'count_tags()>{self.r.randint(0, 6)}',
            f'id()%{self.r.randint(2, 5)}==0',
            'is_closed()',
            f'length()>{self.r.choice([10, 100, 1000])}',
            f'number(t[{self.quote(key)}])>{self.r.randint(0, 50)}',
            f'is_tag({self.quote(key)})',
            f'version()>{self.r.randint(1, 5)}',
        ])

    def new_set(self, kind):
        name = f's{len(self.sets)}'
        self.sets.append((name, kind))
        return name

    def existing_set(self, kinds=None):
        candidates = [s for s in self.sets if kinds is None or s[1] in kinds]
        return self.pick(candidates) if candidates else None

    # ----- statements -------------------------------------------------------

    def anchored_query(self):
        """A query statement with at least one selective filter, so its result stays bounded."""
        kind = self.r.choice(['node', 'node', 'way', 'way', 'rel', 'nwr', 'nw', 'wr'])
        anchor = self.r.random()
        filters = []
        if anchor < 0.3 and kind in ('node', 'way', 'rel'):
            filters.append(self.ids(kind))
        elif anchor < 0.55:
            key, value = self.tag()
            filters.append(self.tag_filter() if self.r.random() < 0.3 else
                           f'[{self.quote(key)}={self.quote(value)}]')
            filters.append(self.bbox(0.2))
        elif anchor < 0.75:
            filters.append(self.bbox())
        elif anchor < 0.85:
            filters.append(self.around_point())
        else:
            key, value = self.pick(self.v['rare_tags'])
            filters.append(f'[{self.quote(key)}={self.quote(value)}]')
        for _ in range(self.r.randint(0, 2)):
            extra = self.r.random()
            if extra < 0.6:
                filters.append(self.tag_filter())
            elif extra < 0.75:
                filters.append(self.bbox(0.3))
            elif extra < 0.85 and kind in ('node', 'way', 'rel'):
                filters.append(self.ids(kind))
            else:
                filters.append(f'(if:{self.evaluator()})')
        self.r.shuffle(filters)
        out_kind = {'nwr': 'any', 'nw': 'any', 'wr': 'any'}.get(kind, kind)
        return f'{kind}{"".join(filters)}->.{self.new_set(out_kind)};'

    def derived(self):
        """A statement that builds on an earlier set."""
        name, kind = self.existing_set()
        choice = self.r.random()
        if choice < 0.2:
            arrow = self.r.choice(['>', '>>', '<', '<<'])
            return f'.{name} {arrow}->.{self.new_set("any")};'
        if choice < 0.45:
            # Member and parent filters: the main users of id predicates.
            forms = [('node', f'(w.{name})'), ('node', f'(r.{name})'), ('way', f'(r.{name})'),
                     ('rel', f'(r.{name})'), ('way', f'(bn.{name})'), ('rel', f'(bn.{name})'),
                     ('rel', f'(bw.{name})'), ('rel', f'(br.{name})'), ('wr', f'(bn.{name})')]
            if self.v['roles'] and self.r.random() < 0.3:
                role = self.quote(self.pick(self.v['roles']))
                forms += [('node', f'(r.{name}:{role})'), ('way', f'(r.{name}:{role})'),
                          ('rel', f'(bw.{name}:{role})')]
            if self.r.random() < 0.2:
                forms.append(('node', f'(w.{name}:{self.r.choice(["1", "-1", "1,-1", "2"])})'))
            target, filt = self.pick(forms)
            extra = self.tag_filter() if self.r.random() < 0.3 else ''
            return f'{target}{filt}{extra}->.{self.new_set(target)};'
        if choice < 0.6:
            target = kind if kind in ('node', 'way', 'rel') else self.r.choice(['node', 'way', 'rel'])
            filt = self.r.choice([self.tag_filter(), self.bbox(0.3), f'(if:{self.evaluator()})',
                                  self.ids(target)])
            return f'{target}.{name}{filt}->.{self.new_set(target)};'
        if choice < 0.68:
            target = self.r.choice(['node', 'way'])
            return (f'{target}(around.{name}:{self.r.choice([0, 20, 100, 300])})'
                    f'{self.tag_filter()}->.{self.new_set(target)};')
        if choice < 0.8 and len(self.sets) >= 2:
            other, _ = self.existing_set()
            if self.r.random() < 0.6:
                return f'(.{name}; .{other};)->.{self.new_set("any")};'
            return f'(.{name}; - .{other};)->.{self.new_set(kind)};'
        if choice < 0.86:
            return f'node(way_cnt.{name}:{self.r.choice(["2-", "1", "0-1", "3-"])})->.{self.new_set("node")};'
        if choice < 0.92:
            return f'node(way_link.{name}:{self.r.choice(["2-", "3-", "1"])})->.{self.new_set("node")};'
        return self.anchored_query()

    def out_statement(self, name):
        if self.r.random() < 0.15:
            return f'.{name} out count;'
        tokens = [self.r.choice(['ids', 'skel', 'body', 'tags', 'meta'])]
        if self.r.random() < 0.35:
            tokens.append(self.r.choice(['geom', 'bb', 'center']))
        if self.r.random() < 0.3:
            tokens.append(self.r.choice(['qt', 'asc']))
        if self.r.random() < 0.2:
            tokens.append(str(self.r.randint(1, 50)))
        if self.r.random() < 0.1:
            tokens.append(self.bbox(0.1))
        return f'.{name} out {" ".join(tokens)};'

    def query(self):
        header = '[timeout:60][maxsize:536870912]'
        fmt = self.r.random()
        if fmt < 0.45:
            header += '[out:json]'
        elif fmt < 0.6:
            header += '[out:csv(::type,::id,::lat,::lon,' + self.quote(self.pick(self.v['keys'])) + ')]'
        if self.r.random() < 0.1:
            header += '[bbox:' + self.bbox(0.3)[1:-1] + ']'
        lines = [header + ';', self.anchored_query()]
        for _ in range(self.r.randint(0, 4)):
            lines.append(self.derived())
        if self.r.random() < 0.08:
            name, _ = self.existing_set()
            lines.append(f'foreach.{name}->.it{{ .it out ids; (.it; .it >;)->.x; .x out count; }}')
        if self.r.random() < 0.05:
            name, _ = self.existing_set()
            lines.append(f'.{name} convert row ::id=id(),tags=count_tags();')
            lines.append('out;')
        outs = sorted({self.pick(self.sets)[0] for _ in range(self.r.randint(1, 3))})
        lines.extend(self.out_statement(name) for name in outs)
        return '\n'.join(lines) + '\n'


def generate(vocab, seed, index):
    return Generator(vocab, random.Random(f'{seed}-{index}')).query()


def run(bindir, db, query, timeout):
    """Run one query; return (status, digest, raw_output)."""
    try:
        proc = subprocess.run([os.path.join(bindir, 'osm3s_query'), '--db-dir=' + db.rstrip('/') + '/'],
                              input=query.encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return 'timeout', None, b''
    if RESOURCE_LIMIT.search(proc.stdout[-4096:]) and b'Timeout is' not in proc.stdout[-4096:]:
        return 'resource-limit', None, proc.stdout
    stable = b'\n'.join(line for line in proc.stdout.split(b'\n') if not VOLATILE.search(line))
    status = 'ok' if proc.returncode == 0 else f'exit-{proc.returncode}'
    if b'runtime error' in proc.stdout or b'static error' in proc.stdout or b'parse error' in proc.stdout:
        status = 'query-error'
    digest = hashlib.sha256(status.encode() + b'\0' + stable).hexdigest()
    return status, digest, proc.stdout


def check(args_tuple):
    a, b, db, vocab, seed, index, timeout = args_tuple
    query = generate(vocab, seed, index)
    status_a, digest_a, out_a = run(a, db, query, timeout)
    status_b, digest_b, out_b = run(b, db, query, timeout)
    if 'timeout' in (status_a, status_b) or 'resource-limit' in (status_a, status_b):
        verdict = 'skipped'
    elif digest_a == digest_b:
        verdict = 'same'
    else:
        verdict = 'DIFFERENT'
    empty = status_a == 'ok' and out_a.count(b'\n') <= 6
    return index, verdict, status_a, status_b, empty, query, out_a, out_b


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--vocab', required=True, help='vocabulary from fuzz_vocab.py')
    parser.add_argument('--seed', type=int, default=1)
    parser.add_argument('--show', type=int, metavar='INDEX', help='print query INDEX of --seed and exit')
    parser.add_argument('--a', help='bin directory of the baseline build')
    parser.add_argument('--b', help='bin directory of the build under test')
    parser.add_argument('--db', help='database directory')
    parser.add_argument('--start', type=int, default=0, help='first query index (default 0)')
    parser.add_argument('--count', type=int, default=1000)
    parser.add_argument('--jobs', type=int, default=os.cpu_count())
    parser.add_argument('--timeout', type=int, default=30, help='seconds per run before giving up')
    parser.add_argument('--out-dir', default='fuzz-failures')
    args = parser.parse_args()

    with open(args.vocab, 'rb') as f:
        raw = f.read()
    vocab = json.loads(raw)
    if args.show is not None:
        sys.stdout.write(generate(vocab, args.seed, args.show))
        return 0
    if not (args.a and args.b and args.db):
        parser.error('--a, --b and --db are required unless --show is given')

    print(f'seed {args.seed}, queries {args.start}..{args.start + args.count - 1}, '
          f'vocab sha256 {hashlib.sha256(raw).hexdigest()[:12]}')
    verdicts = collections.Counter()
    statuses = collections.Counter()
    empty = 0
    tasks = [(args.a, args.b, args.db, vocab, args.seed, i, args.timeout)
             for i in range(args.start, args.start + args.count)]
    with concurrent.futures.ProcessPoolExecutor(max_workers=args.jobs) as pool:
        for done, result in enumerate(pool.map(check, tasks, chunksize=4), 1):
            index, verdict, status_a, status_b, is_empty, query, out_a, out_b = result
            verdicts[verdict] += 1
            statuses[status_a] += 1
            empty += is_empty
            if verdict == 'DIFFERENT':
                path = os.path.join(args.out_dir, f'{args.seed}-{index}')
                os.makedirs(path, exist_ok=True)
                for name, data in (('query.ql', query.encode()), ('a.out', out_a), ('b.out', out_b)):
                    with open(os.path.join(path, name), 'wb') as f:
                        f.write(data)
                print(f'DIFFERENT: seed {args.seed} query {index} ({status_a} vs {status_b}) -> {path}')
            if done % 500 == 0:
                print(f'  {done}/{len(tasks)} done: {dict(verdicts)}', flush=True)
    print(f'verdicts: {dict(verdicts)}')
    print(f'baseline outcomes: {dict(statuses)}; ok but empty: {empty}')
    return 1 if verdicts['DIFFERENT'] else 0


if __name__ == '__main__':
    sys.exit(main())
