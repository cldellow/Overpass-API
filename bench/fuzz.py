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
import resource
import subprocess
import sys

# Lines that name the build, so they differ between builds without the data differing.
VOLATILE = re.compile(rb'(generator|Overpass API \d|"version":)')
# Outcomes that depend on timing or memory, not on the code under test.
RESOURCE_LIMIT = re.compile(rb'(timed out|out of memory|Timeout is|Query run out of memory)', re.I)


class Generator:
    """Build one query from a seeded random source and the vocabulary."""

    def __init__(self, vocab, rng, features=frozenset()):
        self.v = vocab
        self.r = rng
        # Optional statement families, for databases that support them:
        #   areas    area queries, is_in, map_to_area, pivot (needs areas built by the area rules)
        #   history  [date:], [diff:], [adiff:], newer, changed, retro, timeline (needs attic data)
        #   users    user, uid, user_touched, uid_touched (needs user metadata)
        #   complete complete loops
        self.features = features
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
        choices = [
            f't[{self.quote(key)}]=={self.quote(value)}',
            f'count_tags()>{self.r.randint(0, 6)}',
            f'id()>{self.r.randint(1, 9)}000000000',
            'is_closed()',
            f'length()>{self.r.choice([10, 100, 1000])}',
            f'number(t[{self.quote(key)}])>{self.r.randint(0, 50)}',
            f'is_tag({self.quote(key)})',
            f'version()>{self.r.randint(1, 5)}',
            f'date(timestamp())>date("{self.r.randint(2008, 2026)}-01-01")',
            f'changeset()>{self.r.randint(1, 170)}000000',
            f'uid()>{self.r.randint(1, 25)}000000',
        ]
        if 'users' in self.features and self.v.get('users'):
            choices.append(f'user()=={self.quote(self.pick(self.v["users"]))}')
        return self.r.choice(choices)

    def when(self):
        """A timestamp within the database's history, from the vocabulary's edit times."""
        return self.quote(self.pick(self.v['edit_times']))

    def time_pair(self):
        a, b = sorted([self.pick(self.v['edit_times']), self.pick(self.v['edit_times'])])
        return self.quote(a), self.quote(b)

    def user_filter(self):
        form = self.r.random()
        if form < 0.3:
            return f'(user:{self.quote(self.pick(self.v["users"]))})'
        if form < 0.6:
            return f'(uid:{self.pick(self.v["uids"])})'
        if form < 0.8:
            return f'(user_touched:{self.quote(self.pick(self.v["users"]))})'
        return f'(uid_touched:{self.pick(self.v["uids"])})'

    def history_filter(self):
        if self.r.random() < 0.5:
            return f'(newer:{self.when()})'
        a, b = self.time_pair()
        return f'(changed:{a},{b})' if self.r.random() < 0.7 else f'(changed:{a})'

    def area_query(self):
        """An area set: by name, or by containment of a point."""
        if self.r.random() < 0.75 and self.v.get('area_names'):
            name = self.quote(self.pick(self.v['area_names']))
            extra = '' if self.r.random() < 0.7 else self.r.choice(['[admin_level]', '[boundary]', '["type"="multipolygon"]'])
            return f'area[name={name}]{extra}->.{self.new_set("area")};'
        lat, lon = self.pick(self.v['node_coords'])
        return f'is_in({lat:.6f},{lon:.6f})->.{self.new_set("area")};'

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
        if 'users' in self.features and self.v.get('users') and self.r.random() < 0.15:
            filters.append(self.user_filter())
        if 'history' in self.features and self.v.get('edit_times') and self.r.random() < 0.15:
            filters.append(self.history_filter())
        self.r.shuffle(filters)
        out_kind = {'nwr': 'any', 'nw': 'any', 'wr': 'any'}.get(kind, kind)
        return f'{kind}{"".join(filters)}->.{self.new_set(out_kind)};'

    def derived(self):
        """A statement that builds on an earlier set."""
        name, kind = self.existing_set()
        if self.features and self.r.random() < 0.3:
            extra = self.feature_statement(name, kind)
            if extra:
                return extra
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
            return f'node(way_cnt.{name}:{self.r.choice(["2-", "1", "1-2", "3-"])})->.{self.new_set("node")};'
        if choice < 0.92:
            return f'node(way_link.{name}:{self.r.choice(["2-", "3-", "1"])})->.{self.new_set("node")};'
        extra = self.feature_statement(name, kind)
        return extra if extra else self.anchored_query()

    def feature_statement(self, name, kind):
        """A statement from an enabled optional family, or None."""
        options = []
        if 'areas' in self.features:
            options += ['area_filter', 'area_filter', 'is_in_set', 'map_to_area', 'pivot']
        if 'complete' in self.features:
            options.append('complete')
        if not options:
            return None
        pick = self.r.choice(options)
        if pick == 'area_filter':
            area_stmt = self.area_query()
            area_name = self.sets[-1][0]
            target = self.r.choice(['node', 'way', 'rel', 'nwr'])
            key, value = self.pick(self.v['rare_tags'] if self.r.random() < 0.6 else self.v['common_tags'])
            filt = f'[{self.quote(key)}={self.quote(value)}]' if self.r.random() < 0.8 else self.bbox(0.1)
            return f'{area_stmt}\n{target}(area.{area_name}){filt}->.{self.new_set(target)};'
        if pick == 'is_in_set':
            return f'.{name} is_in->.{self.new_set("area")};'
        if pick == 'map_to_area':
            return f'.{name} map_to_area->.{self.new_set("area")};'
        if pick == 'pivot':
            areas = [s for s in self.sets if s[1] == 'area']
            if not areas:
                return None
            target = self.r.choice(['way', 'rel', 'nwr'])
            return f'{target}(pivot.{self.pick(areas)[0]})->.{self.new_set(target)};'
        if pick == 'complete':
            key = self.quote(self.pick(self.v['keys'][:30]))
            body = self.r.choice([f'way(bn)[{key}]; node(w);', f'way(around:0)[{key}];', f'node(w); way(bn)[{key}];'])
            return f'complete({self.r.randint(1, 6)}).{name}->.{self.new_set("any")} {{ {body} }}'
        return None

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
        if 'history' in self.features and self.v.get('edit_times'):
            mode = self.r.random()
            if mode < 0.25:
                header += f'[date:{self.when()}]'
            elif mode < 0.5 and '[out:' not in header:
                # diff and adiff need XML output (the default).
                a, b = self.time_pair()
                if mode < 0.4:
                    header += f'[diff:{a},{b}]' if self.r.random() < 0.7 else f'[diff:{a}]'
                else:
                    header += f'[adiff:{a},{b}]'
        lines = [header + ';', self.anchored_query()]
        if 'history' in self.features and self.v.get('edit_times') and self.r.random() < 0.08:
            kind = self.r.choice(['node', 'way', 'relation'])
            pool = {'node': 'node_ids', 'way': 'way_ids', 'relation': 'relation_ids'}[kind]
            version = f',{self.r.randint(1, 4)}' if self.r.random() < 0.5 else ''
            lines.append(f'timeline({kind},{self.pick(self.v[pool])}{version})->.{self.new_set("derived")};')
        if 'history' in self.features and self.v.get('edit_times') and self.r.random() < 0.06:
            inner = self.anchored_query()
            inner_name = self.sets[-1][0]
            lines.append(f'retro(date({self.when()})) {{ {inner} .{inner_name} out ids; }}')
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


def generate(vocab, seed, index, features=frozenset()):
    return Generator(vocab, random.Random(f'{seed}-{index}'), features).query()


def child_cpu():
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    return usage.ru_utime + usage.ru_stime


def run(bindir, db, query, timeout):
    """Run one query; return (status, digest, raw_output, cpu_seconds)."""
    before = child_cpu()
    result = run_once(bindir, db, query, timeout)
    return result + (child_cpu() - before,)


def run_once(bindir, db, query, timeout):
    try:
        proc = subprocess.run([os.path.join(bindir, 'osm3s_query'), '--db-dir=' + db.rstrip('/') + '/'],
                              input=query.encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return 'timeout', None, b''
    # osm3s_query reports errors, including timeouts, on stderr; some formats repeat them in stdout.
    errors = [line for line in proc.stderr.split(b'\n') if b'error' in line.lower()]
    if (RESOURCE_LIMIT.search(proc.stdout[-4096:]) and b'Timeout is' not in proc.stdout[-4096:]) \
            or any(RESOURCE_LIMIT.search(line) for line in errors):
        return 'resource-limit', None, proc.stdout
    stable = b'\n'.join(line for line in proc.stdout.split(b'\n') if not VOLATILE.search(line))
    status = 'ok' if proc.returncode == 0 else f'exit-{proc.returncode}'
    if errors or b'runtime error' in proc.stdout or b'static error' in proc.stdout or b'parse error' in proc.stdout:
        # Keep the first message, without line numbers, so the summary shows which errors occur.
        first = (errors[0] if errors else b'error in output').decode(errors='replace')
        status = 'query-error: ' + re.sub(r'line \d+', 'line N', first)[:90]
    digest = hashlib.sha256(status.encode() + b'\0' + stable + b'\0' + b'\n'.join(errors)).hexdigest()
    return status, digest, proc.stdout


def check(args_tuple):
    a, b, db, db_b, vocab, seed, index, timeout, features = args_tuple
    query = generate(vocab, seed, index, features)
    status_a, digest_a, out_a, cpu_a = run(a, db, query, timeout)
    status_b, digest_b, out_b, cpu_b = run(b, db_b, query, timeout)
    if 'timeout' in (status_a, status_b) or 'resource-limit' in (status_a, status_b):
        verdict = 'skipped'
    elif digest_a == digest_b:
        verdict = 'same'
    else:
        verdict = 'DIFFERENT'
    empty = status_a == 'ok' and out_a.count(b'\n') <= 6
    return index, verdict, status_a, status_b, empty, query, out_a, out_b, cpu_a, cpu_b


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--vocab', required=True, help='vocabulary from fuzz_vocab.py')
    parser.add_argument('--seed', type=int, default=1)
    parser.add_argument('--show', type=int, metavar='INDEX', help='print query INDEX of --seed and exit')
    parser.add_argument('--a', help='bin directory of the baseline build')
    parser.add_argument('--b', help='bin directory of the build under test')
    parser.add_argument('--db', help='database directory')
    parser.add_argument('--db-b', help='database directory for --b (default: --db); with the same build '
                        'for --a and --b, this compares two databases, e.g. to test an import change')
    parser.add_argument('--start', type=int, default=0, help='first query index (default 0)')
    parser.add_argument('--count', type=int, default=1000)
    parser.add_argument('--jobs', type=int, default=os.cpu_count())
    parser.add_argument('--timeout', type=int, default=30, help='seconds per run before giving up')
    parser.add_argument('--out-dir', default='fuzz-failures')
    parser.add_argument('--features', default='',
                        help='comma-separated optional statement families: areas, history, users, complete')
    parser.add_argument('--report', metavar='CSV',
                        help='write one row per query: status, CPU time per build, output size '
                             '(for finding slow or degenerate queries)')
    args = parser.parse_args()

    with open(args.vocab, 'rb') as f:
        raw = f.read()
    vocab = json.loads(raw)
    features = frozenset(f for f in args.features.split(',') if f)
    unknown = features - {'areas', 'history', 'users', 'complete'}
    if unknown:
        parser.error('unknown features: ' + ', '.join(sorted(unknown)))
    if args.show is not None:
        sys.stdout.write(generate(vocab, args.seed, args.show, features))
        return 0
    if not (args.a and args.b and args.db):
        parser.error('--a, --b and --db are required unless --show is given')

    print(f'seed {args.seed}, features {",".join(sorted(features)) or "none"}, queries {args.start}..{args.start + args.count - 1}, '
          f'vocab sha256 {hashlib.sha256(raw).hexdigest()[:12]}')
    verdicts = collections.Counter()
    statuses = collections.Counter()
    empty = 0
    tasks = [(args.a, args.b, args.db, args.db_b or args.db, vocab, args.seed, i, args.timeout, features)
             for i in range(args.start, args.start + args.count)]
    report = None
    if args.report:
        report = open(args.report, 'w')
        report.write('seed,index,verdict,status_a,status_b,cpu_a_ms,cpu_b_ms,b_over_a,out_bytes,cpu_a_ms_per_kb\n')
    with concurrent.futures.ProcessPoolExecutor(max_workers=args.jobs) as pool:
        for done, result in enumerate(pool.map(check, tasks, chunksize=4), 1):
            index, verdict, status_a, status_b, is_empty, query, out_a, out_b, cpu_a, cpu_b = result
            if report:
                size = len(out_a)
                ratio = cpu_b / cpu_a if cpu_a > 0 else 0
                report.write(f'{args.seed},{index},{verdict},{status_a},{status_b},{cpu_a*1000:.1f},'
                             f'{cpu_b*1000:.1f},{ratio:.3f},{size},{cpu_a*1000/max(size/1024, 1):.2f}\n')
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
    if report:
        report.close()
    print(f'verdicts: {dict(verdicts)}')
    plain = collections.Counter({k: v for k, v in statuses.items() if not k.startswith('query-error')})
    errs = {k: v for k, v in statuses.items() if k.startswith('query-error')}
    print(f'baseline outcomes: {dict(plain)}, query errors: {sum(errs.values())}; ok but empty: {empty}')
    for k, v in sorted(errs.items(), key=lambda kv: -kv[1])[:8]:
        print(f'  {v:5}  {k}')
    return 1 if verdicts['DIFFERENT'] else 0


if __name__ == '__main__':
    sys.exit(main())
