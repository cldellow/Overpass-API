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

The baseline's (--a's) outcomes are cached in --cache-dir, keyed by a hash of its osm3s_query
binary, a fingerprint of the database's data files, --timeout and the query text, so later runs
against the same baseline only run --b. Timeouts and memory-limit failures are never cached. When
a query differs and its baseline came from the cache, the baseline is run again to write a.out; if
its fresh outcome doesn't match the cached one, the entry is replaced and counted as stale.
--no-cache runs both builds every time; --clear-cache empties the cache first. Cache entries from
a database derived from licensed data stay on this machine, like the database itself.
"""
import argparse
import collections
import concurrent.futures
import csv
import hashlib
import json
import os
import random
import re
import resource
import shutil
import subprocess
import sys
import tempfile

from osm3s_run import check_rss_support, run_osm3s

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
        #   poi      point-of-interest lookups: JSON unions of statements around one point
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

    def alternation_filter(self):
        """A regex over several values of one key, as client tools write them: osmnx excludes
        lists of values with ["k"!~"a|b|c"], osmdata matches several with ["k"~"^(a|b)$"]."""
        if not hasattr(self, '_values_by_key'):
            self._values_by_key = {}
            for k, v in self.v['common_tags'] + self.v['rare_tags']:
                self._values_by_key.setdefault(k, []).append(v)
        key, value = self.tag()
        values = self._values_by_key.get(key, [value])
        chosen = sorted({self.pick(values) for _ in range(self.r.randint(2, 22))} | {value})
        alternation = '|'.join(re.escape(v) for v in chosen)
        if self.r.random() < 0.6:
            return f'[{self.quote(key)}!~{self.quote(alternation)}]'
        return f'[{self.quote(key)}~{self.quote("^(" + alternation + ")$")}{",i" if self.r.random() < 0.3 else ""}]'

    def tag_filter(self):
        if self.r.random() < 0.08:
            return self.alternation_filter()
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

    def poly(self):
        """A poly: filter: a star-shaped polygon around a node, so its edges never cross.
        Sizes range up to the city boundaries that osmnx sends; quotes vary as clients do."""
        import math
        lat, lon = self.pick(self.v['node_coords'])
        n = self.r.choice([3, 4, 6, 10, 30, 100, 400])
        radius = self.r.uniform(0.002, 0.05)
        angles = sorted(self.r.uniform(0, 2 * math.pi) for _ in range(n))
        points = []
        for a in angles:
            r = radius * self.r.uniform(0.3, 1.0)
            points.append(f'{lat + r * math.sin(a):.6f} {lon + r * 1.4 * math.cos(a):.6f}')
        quote = self.r.choice(['"', "'"])
        return f'(poly:{quote}{" ".join(points)}{quote})'

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
        """An area set: by name, by id (both forms clients send), or by containment of a point."""
        if self.r.random() < 0.3 and self.v.get('area_relation_ids'):
            area_id = 3600000000 + self.pick(self.v['area_relation_ids'])
            form = self.r.choice([f'area(id:{area_id})', f'area({area_id})'])
            return f'{form}->.{self.new_set("area")};'
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
        elif anchor < 0.68:
            filters.append(self.bbox())
        elif anchor < 0.75:
            filters.append(self.poly())
        elif anchor < 0.85:
            filters.append(self.around_point())
        else:
            key, value = self.pick(self.v['rare_tags'])
            filters.append(f'[{self.quote(key)}={self.quote(value)}]')
        for _ in range(self.r.randint(0, 2)):
            extra = self.r.random()
            if extra < 0.6:
                filters.append(self.tag_filter())
            elif extra < 0.7:
                filters.append(self.bbox(0.3))
            elif extra < 0.75:
                filters.append(self.poly())
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

    def values_of(self, key):
        if not hasattr(self, '_values_by_key'):
            self._values_by_key = {}
            for k, v in self.v['common_tags'] + self.v['rare_tags']:
                self._values_by_key.setdefault(k, []).append(v)
        return self._values_by_key.get(key, [])

    def poi_filter(self):
        """Tag filters as POI lookups write them: exact values, keys, and alternations over a
        key's values, unanchored or anchored and often case-insensitive."""
        key, value = self.tag()
        values = sorted({value} | {self.pick(self.values_of(key) or [value]) for _ in range(self.r.randint(1, 8))})
        form = self.r.random()
        ci = ',i' if self.r.random() < 0.3 else ''
        if form < 0.4:
            f = f'[{self.quote(key)}={self.quote(value)}]'
        elif form < 0.55:
            f = f'[{self.quote(key)}]'
        elif form < 0.85:
            f = f'[{self.quote(key)}~{self.quote("|".join(re.escape(v) for v in values))}{ci}]'
        else:
            f = f'[{self.quote(key)}~{self.quote("^(" + "|".join(re.escape(v) for v in values) + ")$")}{ci}]'
        if self.r.random() < 0.1:
            # A second filter on a name-like key, as brand searches add.
            k2 = self.pick(self.v['keys'])
            words = sorted({re.escape(self.tag()[1][:6]) for _ in range(self.r.randint(1, 4))})
            f += f'[{self.quote(k2)}~{self.quote("|".join(words))},i]'
        return f

    def poi_query(self):
        """A point-of-interest lookup, the most common shape among the public servers' heavy
        queries: a JSON union of node, way and relation statements around one point, mostly with
        one shared radius (often large, some from miles), returning centers and tags."""
        lat, lon = self.pick(self.v['node_coords'])
        shared = self.r.choice([500, 1000, 1500, 2000, 3000, 3219, 4828, 5000, 10000, 25000])
        statements = []
        for _ in range(self.r.randint(2, 15)):
            kind = self.r.choice(['node', 'node', 'way', 'way', 'relation', 'nwr'])
            radius = shared if self.r.random() < 0.6 else self.r.choice([500, 1000, 2000, 3000, 5000])
            statements.append(f'{kind}{self.poi_filter()}(around:{radius},{lat:.6f},{lon:.6f});')
        out = self.r.choice(['out center tags;', 'out center;', 'out body center;', 'out tags center;',
                             'out center tags 200;', 'out body center qt;', 'out geom;'])
        return ('[timeout:60][maxsize:536870912][out:json];\n(\n  ' + '\n  '.join(statements)
                + '\n);\n' + out + '\n')

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
        # Checked before any draw, so queries without the feature are unchanged.
        if 'poi' in self.features and self.r.random() < 0.5:
            return self.poi_query()
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


def run(bindir, db, query, timeout, rss=False):
    """Run one query; return (status, digest, raw_output, cpu_seconds, peak_rss_kb or None)."""
    before = child_cpu()
    status, digest, out, peak = run_once(bindir, db, query, timeout, rss)
    return status, digest, out, child_cpu() - before, peak


def run_once(bindir, db, query, timeout, rss=False):
    try:
        returncode, stdout, stderr, peak = run_osm3s(bindir, db, query.encode(), timeout=timeout, rss=rss)
    except subprocess.TimeoutExpired:
        return 'timeout', None, b'', None
    proc = subprocess.CompletedProcess([], returncode, stdout, stderr)
    # osm3s_query reports errors, including timeouts, on stderr; some formats repeat them in stdout.
    errors = [line for line in proc.stderr.split(b'\n') if b'error' in line.lower()]
    if (RESOURCE_LIMIT.search(proc.stdout[-4096:]) and b'Timeout is' not in proc.stdout[-4096:]) \
            or any(RESOURCE_LIMIT.search(line) for line in errors):
        return 'resource-limit', None, proc.stdout, peak
    stable = b'\n'.join(line for line in proc.stdout.split(b'\n') if not VOLATILE.search(line))
    status = 'ok' if proc.returncode == 0 else f'exit-{proc.returncode}'
    if errors or b'runtime error' in proc.stdout or b'static error' in proc.stdout or b'parse error' in proc.stdout:
        # Keep the first message, without line numbers, so the summary shows which errors occur.
        first = (errors[0] if errors else b'error in output').decode(errors='replace')
        status = 'query-error: ' + re.sub(r'line \d+', 'line N', first)[:90]
    digest = hashlib.sha256(status.encode() + b'\0' + stable + b'\0' + b'\n'.join(errors)).hexdigest()
    return status, digest, proc.stdout, peak


DEFAULT_CACHE_DIR = os.path.expanduser('~/.cache/overpass-bench/fuzz')
# Outcomes that depend on load rather than on the build, so they are never cached.
UNCACHEABLE = ('timeout', 'resource-limit')
# Files whose contents are the database. osm3s_query appends to transactions.log on every run,
# so log files must not be part of the fingerprint.
DB_DATA_SUFFIXES = ('.bin', '.idx', '.map')
DB_DATA_NAMES = ('replicate_id', 'osm_base_version', 'area_version')


def file_sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def db_fingerprint(db):
    """Identify a database's contents cheaply: the name, size and modification time of each data file.
    An import or update changes them."""
    h = hashlib.sha256(os.path.realpath(db).encode())
    for name in sorted(os.listdir(db)):
        if not (name.endswith(DB_DATA_SUFFIXES) or name in DB_DATA_NAMES):
            continue
        st = os.stat(os.path.join(db, name))
        h.update(f'\0{name}\0{st.st_size}\0{st.st_mtime_ns}'.encode())
    return h.hexdigest()


def baseline_cache_prefix(bindir, db, timeout):
    """The part of the cache key shared by every query of a run."""
    return f'{file_sha(os.path.join(bindir, "osm3s_query"))}|{db_fingerprint(db)}|{timeout}'


def cache_path(cache_dir, prefix, query):
    key = hashlib.sha256(f'{prefix}\0{query}'.encode()).hexdigest()
    return os.path.join(cache_dir, key[:2], key + '.json')


def cache_load(path, need_rss):
    try:
        with open(path) as f:
            entry = json.load(f)
    except (OSError, ValueError):
        return None
    if need_rss and entry.get('rss_kb') is None:
        return None
    return entry


def cache_store(path, status, digest, out, cpu, rss_kb):
    if status in UNCACHEABLE:
        return
    entry = {'status': status, 'digest': digest, 'out_bytes': len(out),
             'empty': status == 'ok' and out.count(b'\n') <= 6, 'cpu': cpu, 'rss_kb': rss_kb}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # Write and rename, so parallel workers and interrupted runs never leave a partial entry.
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix='.tmp')
    with os.fdopen(fd, 'w') as f:
        json.dump(entry, f)
    os.replace(tmp, path)


def check(args_tuple):
    a, b, db, db_b, vocab, seed, index, timeout, features, rss, cache_dir, cache_prefix = args_tuple
    query = generate(vocab, seed, index, features)
    path = cache_path(cache_dir, cache_prefix, query) if cache_dir else None
    entry = cache_load(path, rss) if path else None
    stale = False
    if entry:
        status_a, digest_a, out_a, cpu_a, rss_a = entry['status'], entry['digest'], None, entry['cpu'], entry['rss_kb']
        out_bytes_a, empty = entry['out_bytes'], entry['empty']
    else:
        status_a, digest_a, out_a, cpu_a, rss_a = run(a, db, query, timeout, rss)
        if path:
            cache_store(path, status_a, digest_a, out_a, cpu_a, rss_a)
        out_bytes_a, empty = len(out_a), status_a == 'ok' and out_a.count(b'\n') <= 6
    status_b, digest_b, out_b, cpu_b, rss_b = run(b, db_b, query, timeout, rss)
    if 'timeout' in (status_a, status_b) or 'resource-limit' in (status_a, status_b):
        verdict = 'skipped'
    elif digest_a == digest_b:
        verdict = 'same'
    else:
        verdict = 'DIFFERENT'
    if verdict == 'DIFFERENT' and entry:
        # Run the baseline again: a.out needs its output, and a cached outcome that no longer
        # holds (an undetected database change, or a non-deterministic baseline) must not
        # pass for a difference in --b.
        status_a, fresh_digest, out_a, cpu_a, rss_a = run(a, db, query, timeout, rss)
        out_bytes_a, empty = len(out_a), status_a == 'ok' and out_a.count(b'\n') <= 6
        if fresh_digest != digest_a:
            stale = True
            digest_a = fresh_digest
            if status_a not in UNCACHEABLE:
                cache_store(path, status_a, digest_a, out_a, cpu_a, rss_a)
            elif os.path.exists(path):
                os.unlink(path)
            if 'timeout' in (status_a, status_b) or 'resource-limit' in (status_a, status_b):
                verdict = 'skipped'
            elif digest_a == digest_b:
                verdict = 'same'
    return (index, verdict, status_a, status_b, empty, query, out_a, out_b, cpu_a, cpu_b, rss_a, rss_b,
            out_bytes_a, entry is not None and out_a is None, stale)


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
                        help='comma-separated optional statement families: areas, history, users, complete, poi')
    parser.add_argument('--report', metavar='CSV',
                        help='write one row per query: status, CPU time per build, output size '
                             '(for finding slow or degenerate queries)')
    parser.add_argument('--rss', action='store_true',
                        help='run each query under GNU time and add each build\'s peak memory (RSS) '
                             'to the --report rows (for finding memory-hungry queries)')
    parser.add_argument('--cache-dir', default=DEFAULT_CACHE_DIR,
                        help=f'where baseline (--a) outcomes are cached (default {DEFAULT_CACHE_DIR})')
    parser.add_argument('--no-cache', action='store_true',
                        help='run the baseline for every query, and neither read nor write the cache')
    parser.add_argument('--clear-cache', action='store_true', help='empty --cache-dir before running')
    args = parser.parse_args()

    with open(args.vocab, 'rb') as f:
        raw = f.read()
    vocab = json.loads(raw)
    features = frozenset(f for f in args.features.split(',') if f)
    unknown = features - {'areas', 'history', 'users', 'complete', 'poi'}
    if unknown:
        parser.error('unknown features: ' + ', '.join(sorted(unknown)))
    if args.show is not None:
        sys.stdout.write(generate(vocab, args.seed, args.show, features))
        return 0
    if not (args.a and args.b and args.db):
        parser.error('--a, --b and --db are required unless --show is given')
    if args.rss:
        check_rss_support()

    print(f'seed {args.seed}, features {",".join(sorted(features)) or "none"}, queries {args.start}..{args.start + args.count - 1}, '
          f'vocab sha256 {hashlib.sha256(raw).hexdigest()[:12]}')
    cache_dir = cache_prefix = None
    if args.clear_cache and os.path.isdir(args.cache_dir):
        shutil.rmtree(args.cache_dir)
    if not args.no_cache:
        cache_dir = args.cache_dir
        cache_prefix = baseline_cache_prefix(args.a, args.db, args.timeout)
    verdicts = collections.Counter()
    statuses = collections.Counter()
    empty = 0
    reused = stale = 0
    tasks = [(args.a, args.b, args.db, args.db_b or args.db, vocab, args.seed, i, args.timeout, features,
              args.rss, cache_dir, cache_prefix)
             for i in range(args.start, args.start + args.count)]
    report = None
    if args.report:
        report = open(args.report, 'w', newline='')
        report_csv = csv.writer(report)
        # a_cached is last, so readers of the earlier columns are unaffected. A cached row's
        # cpu_a_ms and rss_a_kb come from the run that filled the cache.
        report_csv.writerow(['seed', 'index', 'verdict', 'status_a', 'status_b', 'cpu_a_ms', 'cpu_b_ms', 'b_over_a',
                             'out_bytes', 'cpu_a_ms_per_kb']
                            + (['rss_a_kb', 'rss_b_kb', 'rss_b_over_a'] if args.rss else []) + ['a_cached'])
    with concurrent.futures.ProcessPoolExecutor(max_workers=args.jobs) as pool:
        for done, result in enumerate(pool.map(check, tasks, chunksize=4), 1):
            (index, verdict, status_a, status_b, is_empty, query, out_a, out_b, cpu_a, cpu_b, rss_a, rss_b,
             size, a_cached, is_stale) = result
            reused += a_cached
            stale += is_stale
            if report:
                ratio = cpu_b / cpu_a if cpu_a > 0 else 0
                # Statuses carry error messages, which can contain commas: csv quotes them.
                row = [args.seed, index, verdict, status_a, status_b, f'{cpu_a*1000:.1f}', f'{cpu_b*1000:.1f}',
                       f'{ratio:.3f}', size, f'{cpu_a*1000/max(size/1024, 1):.2f}']
                if args.rss:
                    row += [rss_a or '', rss_b or '', f'{rss_b / rss_a:.3f}' if rss_a and rss_b else '']
                row.append(int(a_cached))
                report_csv.writerow(row)
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
    if cache_dir:
        print(f'baseline: {reused} of {len(tasks)} outcomes reused from {cache_dir}'
              + (f'; {stale} stale entries replaced' if stale else ''))
    plain = collections.Counter({k: v for k, v in statuses.items() if not k.startswith('query-error')})
    errs = {k: v for k, v in statuses.items() if k.startswith('query-error')}
    print(f'baseline outcomes: {dict(plain)}, query errors: {sum(errs.values())}; ok but empty: {empty}')
    for k, v in sorted(errs.items(), key=lambda kv: -kv[1])[:8]:
        print(f'  {v:5}  {k}')
    return 1 if verdicts['DIFFERENT'] else 0


if __name__ == '__main__':
    sys.exit(main())
