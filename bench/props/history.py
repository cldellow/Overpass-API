"""History (attic) data for props.py.

A seed's history starts from its normal synthetic data, all at version 1 in 2019, then applies
three rounds of edits (2020, 2021, 2022): nodes moved (some by a single unit, across box and
polygon edges), tags changed, nodes and ways deleted and created, way node lists and relation
members changed. It is written as one full-history file (every version of every object,
deletions as visible="false") and imported with --keep-attic.

The expected state at a date D is, for each object, its latest version with timestamp <= D,
if that version is visible. A way's geometry at D uses its nodes' positions at D.
"""

import random

import props
from props import deg, els, bbox_str, poly_str, classify_point, classify_line, MARGIN

T0 = '2019-01-01T00:00:00Z'
EDITS = ['2020-01-01T00:00:00Z', '2021-01-01T00:00:00Z', '2022-01-01T00:00:00Z']
# Dates queried: before any data, each edit time, a second either side of it, in between,
# and after the last edit.
DATES = ['2018-06-01T00:00:00Z', T0, '2019-06-01T00:00:00Z', '2019-12-31T23:59:59Z',
         EDITS[0], '2020-01-01T00:00:01Z', '2020-07-01T00:00:00Z', EDITS[1], '2021-06-01T00:00:00Z',
         '2021-12-31T23:59:59Z', EDITS[2], '2023-01-01T00:00:00Z']
USERS = props.USERS + [(31, 'erin'), (32, 'frank')]


class History:
    """versions[(kind, id)] = list of dicts with version, ts, uid, user, visible and the
    object's content: pos (nodes), nds (ways) or members (relations), and tags."""

    def __init__(self):
        self.versions = {}
        self.scale = None

    def add(self, kind, i, ts, uid, user, visible=True, **content):
        vs = self.versions.setdefault((kind, i), [])
        v = {'version': len(vs) + 1, 'ts': ts, 'uid': uid, 'user': user, 'visible': visible}
        v.update(content)
        vs.append(v)

    def latest(self, kind, i):
        return self.versions[(kind, i)][-1]

    def at(self, kind, i, date):
        """The version valid at date, or None if the object doesn't exist then."""
        found = None
        for v in self.versions[(kind, i)]:
            if v['ts'] <= date:
                found = v
        return found if found and found['visible'] else None

    def ids(self, kind):
        return sorted(i for k, i in self.versions if k == kind)

    def alive(self, kind, date):
        return {i: v for i in self.ids(kind) for v in [self.at(kind, i, date)] if v}

    def way_pts(self, i, date):
        w = self.at('way', i, date)
        pts = []
        for n in w['nds']:
            nv = self.at('node', n, date)
            if nv:
                pts.append(nv['pos'])
        return pts

    def to_xml(self):
        def esc(v):
            return (str(v).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                    .replace('"', '&quot;').replace('\n', '&#10;').replace('\t', '&#9;'))

        def head(kind, i, v):
            return (f'<{kind} id="{i}" version="{v["version"]}" timestamp="{v["ts"]}" '
                    f'changeset="{v["version"]}" uid="{v["uid"]}" user="{esc(v["user"])}" '
                    f'visible="{"true" if v["visible"] else "false"}"')

        def tags(t):
            return ''.join(f'<tag k="{esc(k)}" v="{esc(val)}"/>' for k, val in sorted(t.items()))

        out = ['<?xml version="1.0" encoding="UTF-8"?>', '<osm version="0.6" generator="props.py history">']
        for kind in ('node', 'way', 'relation'):
            for i in self.ids(kind):
                for v in self.versions[(kind, i)]:
                    if kind == 'node':
                        pos = f' lat="{deg(v["pos"][0])}" lon="{deg(v["pos"][1])}"' if v['visible'] else ''
                        out.append(head(kind, i, v) + pos + '>' + (tags(v['tags']) if v['visible'] else '') + '</node>')
                    elif kind == 'way':
                        body = ''.join(f'<nd ref="{n}"/>' for n in v['nds']) + tags(v['tags']) if v['visible'] else ''
                        out.append(head(kind, i, v) + '>' + body + '</way>')
                    else:
                        body = (''.join(f'<member type="{t}" ref="{r}" role="{esc(role)}"/>' for t, r, role in v['members'])
                                + tags(v['tags'])) if v['visible'] else ''
                        out.append(head(kind, i, v) + '>' + body + '</relation>')
        out.append('</osm>')
        return '\n'.join(out) + '\n'


def generate_history(seed):
    base = props.generate(seed)
    rnd = random.Random(seed * 104729 + 3)
    h = History()
    h.scale = base.scale
    h.origin = base.origin
    h.polygons = base.polygons
    s = dict(props.SCALES)[base.scale]

    def user():
        return rnd.choice(USERS)

    for i, p in base.nodes.items():
        if i in getattr(base, 'special', ()):
            continue
        uid, name = user()
        h.add('node', i, T0, uid, name, pos=p, tags=dict(base.node_tags.get(i, {})))
    for i, nds in base.ways.items():
        uid, name = user()
        h.add('way', i, T0, uid, name, nds=list(nds), tags=dict(base.way_tags.get(i, {})))
    for i, mems in base.rels.items():
        uid, name = user()
        h.add('relation', i, T0, uid, name, members=list(mems), tags=dict(base.rel_tags.get(i, {})))

    next_node = max(base.nodes) + 1
    next_way = max(base.ways) + 1

    for ts in EDITS:
        nodes = [i for i in h.ids('node') if h.latest('node', i)['visible']]
        ways = [i for i in h.ids('way') if h.latest('way', i)['visible']]
        used = {n for w in ways for n in h.latest('way', w)['nds']}
        used |= {r for rel in h.ids('relation') for t, r, _ in h.latest('relation', rel)['members'] if t == 'node'}
        in_rel = {r for rel in h.ids('relation') for t, r, _ in h.latest('relation', rel)['members'] if t == 'way'}

        edited = set()

        def edit(kind, i, **change):
            edited.add((kind, i))
            prev = h.latest(kind, i)
            content = {k: prev[k] for k in ('pos', 'nds', 'members', 'tags') if k in prev}
            content.update(change)
            uid, name = user()
            h.add(kind, i, ts, uid, name, **content)

        # Moves: mostly by a unit or two (across edges), some to a new grid point.
        for i in rnd.sample(nodes, max(1, len(nodes) // 6)):
            lat, lon = h.latest('node', i)['pos']
            if rnd.random() < 0.7:
                d = rnd.choice([-2, -1, 1, 2])
                pos = (lat + d, lon) if rnd.random() < 0.5 else (lat, lon + d)
            else:
                pos = (base.origin[0] + rnd.randint(-1, 17) * s, base.origin[1] + rnd.randint(-1, 17) * s)
            edit('node', i, pos=pos)
        # Tag edits.
        for i in rnd.sample([n for n in nodes if ('node', n) not in edited], max(1, len(nodes) // 15)):
            tags = dict(h.latest('node', i)['tags'])
            if 'amenity' in tags and rnd.random() < 0.5:
                del tags['amenity']
            else:
                tags['amenity'] = rnd.choice(['cafe', 'bench'])
            edit('node', i, tags=tags)
        # Delete unused nodes, delete ways not in a relation.
        deletable = [n for n in nodes if n not in used and ('node', n) not in edited]
        for i in rnd.sample(deletable, min(3, len(deletable))):
            uid, name = user()
            h.add('node', i, ts, uid, name, visible=False)
        for i in rnd.sample([w for w in ways if w not in in_rel], 1):
            uid, name = user()
            h.add('way', i, ts, uid, name, visible=False)
        # Change some ways' node lists (only to nodes that stay visible).
        alive = [n for n in nodes if h.latest('node', n)['visible']]
        for i in rnd.sample([w for w in ways if h.latest('way', w)['visible'] and w not in in_rel], 2):
            edit('way', i, nds=rnd.sample(alive, rnd.randint(2, 4)))
        # New nodes and a new way.
        new = []
        for _ in range(3):
            uid, name = user()
            pos = (base.origin[0] + rnd.randint(-1, 17) * s + rnd.randint(-2, 2),
                   base.origin[1] + rnd.randint(-1, 17) * s + rnd.randint(-2, 2))
            h.add('node', next_node, ts, uid, name, pos=pos, tags={'amenity': 'cafe'} if rnd.random() < 0.3 else {})
            new.append(next_node)
            next_node += 1
        uid, name = user()
        h.add('way', next_way, ts, uid, name, nds=new[:2] + rnd.sample(alive, 1), tags={'highway': 'path'})
        next_way += 1
        # The route relation: swap a way member.
        for rel in h.ids('relation'):
            v = h.latest('relation', rel)
            if v['tags'].get('type') == 'route':
                live_ways = [w for w in h.ids('way') if h.latest('way', w)['visible']]
                mems = [m for m in v['members'] if not (m[0] == 'way' and rnd.random() < 0.3)]
                mems.append(('way', rnd.choice(live_ways), ''))
                edit('relation', rel, members=mems)
    return h


def gen_history_checks(h, seed):
    rnd = random.Random(seed * 7907 + 5)
    checks = []

    def add(name, query, must, must_not=frozenset(), exact=False):
        checks.append((name, query, set(must), set(must_not), exact))

    all_pos = [v['pos'] for i in h.ids('node') for v in h.versions[('node', i)] if v['visible']]
    for date in DATES:
        d = f'[date:"{date}"]'
        nodes = h.alive('node', date)
        ways = h.alive('way', date)
        # bbox around node positions from any time.
        ys = sorted(rnd.choice(all_pos)[0] + rnd.randint(-2, 2) for _ in range(2))
        xs = sorted(rnd.choice(all_pos)[1] + rnd.randint(-2, 2) for _ in range(2))
        s_, n_, w_, e_ = ys[0], ys[1], xs[0], xs[1]
        box = bbox_str(s_, w_, n_, e_)
        inside = {i for i, v in nodes.items() if s_ <= v['pos'][0] <= n_ and w_ <= v['pos'][1] <= e_}
        add('date bbox nodes', f'{d}; node({box});', els('node', inside), exact=True)
        cafes = {i for i in inside if nodes[i]['tags'].get('amenity') == 'cafe'}
        add('date bbox + tag', f'{d}; node[amenity=cafe]({box});', els('node', cafes), exact=True)
        rect = [(s_, w_), (s_, e_), (n_, e_), (n_, w_)]
        must = {('way', i) for i in ways if any(s_ <= p[0] <= n_ and w_ <= p[1] <= e_ for p in h.way_pts(i, date))}
        must_not = set()
        if n_ - s_ > 2 * MARGIN and e_ - w_ > 2 * MARGIN:
            for i in ways:
                pts = h.way_pts(i, date)
                if pts and classify_line(pts, rect) == 'out':
                    must_not.add(('way', i))
        add('date bbox ways', f'{d}; way({box});', must, must_not)
        # Recursion at the date.
        for w in rnd.sample(sorted(ways), min(3, len(ways))):
            add('date way -> nodes', f'{d}; way({w}); node(w);',
                els('node', [n for n in ways[w]['nds'] if n in nodes]), exact=True)
            add('date way > nodes', f'{d}; way({w}); >;',
                els('node', [n for n in ways[w]['nds'] if n in nodes]), exact=True)
        for n in rnd.sample(sorted(nodes), min(3, len(nodes))):
            add('date node -> ways', f'{d}; node({n}); way(bn);',
                {('way', w) for w, v in ways.items() if n in v['nds']}, exact=True)
        rels = h.alive('relation', date)
        for r, v in rels.items():
            mem_ways = [ref for t, ref, _ in v['members'] if t == 'way' and ref in ways]
            mem_nodes = [ref for t, ref, _ in v['members'] if t == 'node' and ref in nodes]
            add('date rel -> ways', f'{d}; relation({r}); way(r);', els('way', mem_ways), exact=True)
            down = set(els('node', mem_nodes)) | set(els('way', mem_ways))
            down |= {('node', n) for wid in mem_ways for n in ways[wid]['nds'] if n in nodes}
            add('date rel >', f'{d}; relation({r}); >;', down, exact=True)
        ids = rnd.sample(h.ids('node'), 6)
        add('date id list', f'{d}; node(id:{",".join(map(str, ids))});',
            els('node', [i for i in ids if i in nodes]), exact=True)
        uid, name = rnd.choice(USERS)
        add('date uid', f'{d}; node(uid:{uid});', {('node', i) for i, v in nodes.items() if v['uid'] == uid}, exact=True)
        # poly: at the date.
        ring = rnd.choice(h.polygons)
        must, must_not = set(), set()
        for i, v in nodes.items():
            c = classify_point(v['pos'], ring)
            if c == 'in':
                must.add(('node', i))
            elif c == 'out':
                must_not.add(('node', i))
        add('date poly nodes', f'{d}; node(poly:"{poly_str(ring)}");', must, must_not)

    # changed: some version timestamped after D1 and at or before D2. For a way, a change of
    # one of its nodes (in any of its versions valid in the interval) counts too. Queried at
    # the current date, so only elements that exist now are returned.
    now_nodes = h.alive('node', '9999')
    now_ways = h.alive('way', '9999')
    for d1, d2 in [(T0, EDITS[0]), ('2019-06-01T00:00:00Z', '2021-06-01T00:00:00Z'), (EDITS[0], EDITS[2]),
                   (EDITS[1], '2021-12-31T23:59:59Z'), ('2018-01-01T00:00:00Z', T0)]:
        def in_range(kind, i):
            return any(d1 < v['ts'] <= d2 for v in h.versions[(kind, i)])

        def moved_in_range(n):
            vs = h.versions[('node', n)]
            return any(d1 < v['ts'] <= d2 and k > 0 and v.get('pos') != vs[k - 1].get('pos')
                       for k, v in enumerate(vs))

        def changed(kind, alive):
            result = {(kind, i) for i in alive if in_range(kind, i)}
            if kind == 'way':
                for i in alive:
                    vs = h.versions[('way', i)]
                    valid = [v for k, v in enumerate(vs)
                             if v['ts'] <= d2 and (k + 1 == len(vs) or vs[k + 1]['ts'] > d1) and v['visible']]
                    if any(moved_in_range(n) for v in valid for n in v['nds']):
                        result.add(('way', i))
            return result
        add('changed nodes', f'node(changed:"{d1}","{d2}");', changed('node', now_nodes), exact=True)
        add('changed ways', f'way(changed:"{d1}","{d2}");', changed('way', now_ways), exact=True)
    # touched: some version by that user, at any time (also after a [date:]: that's what
    # upstream does), of an element that exists at the query date.
    for date in ['9999', EDITS[0], '2021-06-01T00:00:00Z']:
        d = '' if date == '9999' else f'[date:"{date}"]; '
        nodes = h.alive('node', date)
        ways = h.alive('way', date)
        uid, name = rnd.choice(USERS)
        add('uid_touched', f'{d}node(uid_touched:{uid});',
            {('node', i) for i in nodes if any(v['uid'] == uid for v in h.versions[('node', i)])},
            exact=True)
        add('user_touched', f'{d}way(user_touched:"{name}");',
            {('way', i) for i in ways if any(v['user'] == name for v in h.versions[('way', i)])},
            exact=True)
    return checks


# ---------------------------------------------------------------------------------------
# Whole outputs at a past date

def verify_date_ways_geom(date):
    def verify(h, out):
        root, err = props.parse_xml(out)
        if err:
            return err
        problems = []
        ways = h.alive('way', date)
        found = {}
        for e in root.findall('way'):
            i = int(e.get('id'))
            found[i] = e
            if i not in ways:
                continue
            v = ways[i]
            nds = e.findall('nd')
            refs = [int(n.get('ref')) for n in nds]
            if refs != v['nds']:
                problems.append(f'way {i}: nodes {refs}, expected {v["nds"]}')
                continue
            if e.get('version') != str(v['version']):
                problems.append(f'way {i}: version {e.get("version")}, expected {v["version"]}')
            for n in nds:
                nv = h.at('node', int(n.get('ref')), date)
                if nv and n.get('lat') is not None:
                    props.expect_coord(problems, f'way {i} node {n.get("ref")}', n.get('lat'), n.get('lon'), nv['pos'])
        props.expect_ids(problems, 'way', found, ways)
        return problems[:20]
    return verify


def verify_date_nodes_meta(date):
    def verify(h, out):
        root, err = props.parse_xml(out)
        if err:
            return err
        problems = []
        nodes = h.alive('node', date)
        found = {}
        for e in root.findall('node'):
            i = int(e.get('id'))
            found[i] = e
            if i not in nodes:
                continue
            v = nodes[i]
            props.expect_coord(problems, f'node {i}', e.get('lat'), e.get('lon'), v['pos'])
            for k, want in (('version', v['version']), ('timestamp', v['ts']), ('uid', v['uid']), ('user', v['user'])):
                if e.get(k) != str(want):
                    problems.append(f'node {i}: {k} {e.get(k)!r}, expected {want!r}')
            props.expect_tags(problems, f'node {i}', props.xml_tags(e), v['tags'])
        props.expect_ids(problems, 'node', found, nodes)
        return problems[:20]
    return verify


def gen_history_output_checks(h, seed):
    world = props.WORLD
    checks = []
    for date in ('2019-06-01T00:00:00Z', EDITS[1], '2021-06-01T00:00:00Z'):
        checks.append(('date out meta', f'[date:"{date}"];\nnode({world});\nout meta;\n', verify_date_nodes_meta(date)))
        checks.append(('date out geom ways', f'[date:"{date}"];\nway({world});\nout meta geom;\n',
                       verify_date_ways_geom(date)))
    for d1, d2 in ((T0, EDITS[1]), ('2019-06-01T00:00:00Z', '2023-01-01T00:00:00Z')):
        checks.append(('diff', f'[diff:"{d1}","{d2}"];\nnwr({world});\nout meta geom;\n', props.no_verify))
        checks.append(('adiff', f'[adiff:"{d1}","{d2}"];\n(way({world}); node({world}););\nout meta geom;\n',
                       props.no_verify))
    return checks
