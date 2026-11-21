#!/usr/bin/env python3
"""Query-level property tests for Overpass builds.

Each seed generates a small synthetic OSM file built around the configurations that break
geometry code (points on or 1 unit off polygon edges and vertices, axis-parallel segments 0-3
units apart, shared endpoints, data straddling index tile boundaries), imports it, runs
queries against it and checks each result:

  * against an answer computed here from the generated data. Where the boundary semantics
    are subtle (polygons, areas, way geometry, around), the check has a margin: objects
    clearly inside must be returned and objects clearly outside must not; objects within
    the margin of the boundary are left to the comparison below.
  * with --baseline, against the baseline build's output for the same query on the same
    database, byte for byte. On boundary cases the baseline's behaviour is the definition.

Only update_database and osm3s_query are used, so the same seeds replay unchanged against
upstream and any branch. Coordinates are integers in units of 1e-7 degrees, the
resolution Overpass stores.

    python3 bench/props/props.py --bin BUILD/bin --baseline UPSTREAM/bin --seeds 1-40
    python3 bench/props/props.py --seeds 7 --show          # print seed 7's data and queries
"""

import argparse
import concurrent.futures
import hashlib
import math
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time

sys.dont_write_bytecode = True   # don't leave __pycache__ in the checkout
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from osm3s_run import run_osm3s  # noqa: E402

UNIT = 10 ** 7          # units per degree
MARGIN = 2              # units: boundary zone left to the differential check
EARTH_ARC_TO_M = 10 * 1000 * 1000 / (math.pi / 2)   # osm3s's great_circle_dist
AREA_ID_OFFSET = 3600000000

# The area rules from rules/areas.osm3s, so a database gets areas without a source checkout.
AREA_RULES = b"""<?xml version="1.0" encoding="UTF-8"?>
<osm-script timeout="86400" element-limit="4294967296">
<union>
  <query type="relation"><has-kv k="type" v="multipolygon"/><has-kv k="name"/></query>
  <query type="relation"><has-kv k="type" v="boundary"/><has-kv k="name"/></query>
  <query type="relation"><has-kv k="admin_level"/><has-kv k="name"/></query>
  <query type="relation"><has-kv k="postal_code"/></query>
  <query type="relation"><has-kv k="addr:postcode"/></query>
</union>
<foreach into="pivot">
  <union>
    <recurse type="relation-way" from="pivot"/>
    <recurse type="way-node"/>
  </union>
  <make-area pivot="pivot" return-area="no"/>
</foreach>
</osm-script>
"""


# ---------------------------------------------------------------------------------------
# Coordinates

def deg(v):
    """Integer units -> exact decimal degrees string."""
    sign = '-' if v < 0 else ''
    v = abs(v)
    return f'{sign}{v // UNIT}.{v % UNIT:07d}'


# Index tiles have a side of 65536 units in osm3s's internal coordinates
# (ilat = lat_units + 91 * UNIT, ilon = lon_units).
TILE = 65536


def tile_corner_near(lat_units, lon_units):
    ilat = lat_units + 91 * UNIT
    return (round(ilat / TILE) * TILE - 91 * UNIT, round(lon_units / TILE) * TILE)


# ---------------------------------------------------------------------------------------
# Geometry in units (floats)

def seg_point_dist(p, a, b):
    (py, px), (ay, ax), (by, bx) = p, a, b
    dy, dx = by - ay, bx - ax
    if dy == 0 and dx == 0:
        return math.hypot(py - ay, px - ax)
    t = ((py - ay) * dy + (px - ax) * dx) / (dy * dy + dx * dx)
    t = max(0.0, min(1.0, t))
    return math.hypot(py - (ay + t * dy), px - (ax + t * dx))


def orient(a, b, c):
    v = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    return (v > 0) - (v < 0)


def segs_intersect(a, b, c, d):
    o1, o2, o3, o4 = orient(a, b, c), orient(a, b, d), orient(c, d, a), orient(c, d, b)
    if o1 != o2 and o3 != o4:
        return True
    return ((o1 == 0 and seg_point_dist(c, a, b) == 0) or (o2 == 0 and seg_point_dist(d, a, b) == 0)
            or (o3 == 0 and seg_point_dist(a, c, d) == 0) or (o4 == 0 and seg_point_dist(b, c, d) == 0))


def seg_seg_dist(a, b, c, d):
    if segs_intersect(a, b, c, d):
        return 0.0
    return min(seg_point_dist(a, c, d), seg_point_dist(b, c, d),
               seg_point_dist(c, a, b), seg_point_dist(d, a, b))


def ring_edges(ring):
    """Edges of a closed ring given as a vertex list without the repeated first vertex."""
    return [(ring[i], ring[(i + 1) % len(ring)]) for i in range(len(ring))]


def point_in_ring(p, ring):
    inside = False
    y, x = p
    for (ay, ax), (by, bx) in ring_edges(ring):
        if (ax > x) != (bx > x):
            yi = ay + (x - ax) * (by - ay) / (bx - ax)
            if yi < y:
                inside = not inside
    return inside


def classify_point(p, ring):
    """'in' or 'out' if p is more than MARGIN from the ring's boundary, else 'edge'."""
    if min(seg_point_dist(p, a, b) for a, b in ring_edges(ring)) <= MARGIN:
        return 'edge'
    return 'in' if point_in_ring(p, ring) else 'out'


def classify_line(pts, ring):
    """A polyline against a polygon: 'in' if part of it is clearly inside, 'out' if all of
    it is clearly outside, else 'edge'."""
    for p in pts:
        if classify_point(p, ring) == 'in':
            return 'in'
    for a, b in zip(pts, pts[1:]):
        # Sample the segment: a segment can pass through without a vertex inside.
        n = min(400, int(max(abs(b[0] - a[0]), abs(b[1] - a[1]))) + 1)
        for k in range(1, n):
            q = (a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n)
            if classify_point(q, ring) == 'in':
                return 'in'
    if any(point_in_ring(p, ring) for p in pts):
        return 'edge'
    segs = list(zip(pts, pts[1:])) or [(pts[0], pts[0])]
    if min(seg_seg_dist(a, b, c, d) for a, b in segs for c, d in ring_edges(ring)) > MARGIN:
        return 'out'
    return 'edge'


def meters(p, q):
    """Distance between two unit coordinates, on osm3s's sphere (local approximation)."""
    lat = (p[0] + q[0]) / 2 / UNIT
    dy = (p[0] - q[0]) / UNIT
    dx = (p[1] - q[1]) / UNIT * math.cos(math.radians(lat))
    return math.radians(math.hypot(dy, dx)) * EARTH_ARC_TO_M


def meters_to_line(p, pts):
    if len(pts) == 1:
        return meters(p, pts[0])
    best = float('inf')
    lat = p[0] / UNIT
    k = math.cos(math.radians(lat))
    for a, b in zip(pts, pts[1:]):
        # Project to a local plane scaled so that one unit of lon is k units.
        pa, pb = (a[0], a[1] * k), (b[0], b[1] * k)
        d = seg_point_dist((p[0], p[1] * k), pa, pb)
        best = min(best, math.radians(d / UNIT) * EARTH_ARC_TO_M)
    return best


# ---------------------------------------------------------------------------------------
# Synthetic data

class Data:
    def __init__(self):
        self.nodes = {}       # id -> (lat, lon) in units
        self.node_tags = {}
        self.ways = {}        # id -> [node ids]
        self.way_tags = {}
        self.rels = {}        # id -> [(type, ref, role)]
        self.rel_tags = {}
        self.meta = {}        # (type, id) -> (version, timestamp, uid, user)
        self.polygons = []    # rings (vertex lists in units) used for poly: queries
        self.areas = {}       # relation id -> ring

    def node_at(self, p):
        nid = len(self.nodes) + 1
        self.nodes[nid] = p
        return nid

    def way_pts(self, wid):
        return [self.nodes[n] for n in self.ways[wid]]

    def to_xml(self):
        out = ['<?xml version="1.0" encoding="UTF-8"?>', '<osm version="0.6" generator="props.py">']

        def attrs(kind, i):
            v, ts, uid, user = self.meta[(kind, i)]
            return f'id="{i}" version="{v}" timestamp="{ts}" changeset="{v}" uid="{uid}" user="{user}"'

        def tags(t):
            return ''.join(f'<tag k="{k}" v="{v}"/>' for k, v in sorted(t.items()))

        for i, (lat, lon) in sorted(self.nodes.items()):
            out.append(f'<node {attrs("node", i)} lat="{deg(lat)}" lon="{deg(lon)}">'
                       f'{tags(self.node_tags.get(i, {}))}</node>')
        for i, nds in sorted(self.ways.items()):
            out.append(f'<way {attrs("way", i)}>' + ''.join(f'<nd ref="{n}"/>' for n in nds)
                       + tags(self.way_tags.get(i, {})) + '</way>')
        for i, mems in sorted(self.rels.items()):
            out.append(f'<relation {attrs("relation", i)}>'
                       + ''.join(f'<member type="{t}" ref="{r}" role="{role}"/>' for t, r, role in mems)
                       + tags(self.rel_tags.get(i, {})) + '</relation>')
        out.append('</osm>')
        return '\n'.join(out) + '\n'


SCALES = [('micro', 1), ('small', 9), ('tile', 900), ('wide', 20000)]
USERS = [(11, 'alice'), (12, 'bob'), (13, 'carol')]
TIMESTAMPS = ['2019-03-01T00:00:00Z', '2021-06-15T12:00:00Z', '2023-11-30T23:59:59Z']


def random_ring(rnd, g):
    """A simple polygon with vertices on grid g (a function of grid coords -> units)."""
    kind = rnd.choice(['rect', 'tri', 'star', 'L'])
    if kind == 'rect':
        y0, y1 = sorted(rnd.sample(range(0, 17), 2))
        x0, x1 = sorted(rnd.sample(range(0, 17), 2))
        return [g(y0, x0), g(y0, x1), g(y1, x1), g(y1, x0)]
    if kind == 'L':
        y0, y1, y2 = sorted(rnd.sample(range(0, 17), 3))
        x0, x1, x2 = sorted(rnd.sample(range(0, 17), 3))
        return [g(y0, x0), g(y0, x2), g(y1, x2), g(y1, x1), g(y2, x1), g(y2, x0)]
    n = 3 if kind == 'tri' else rnd.randint(4, 7)
    while True:
        pts = list({(rnd.randint(0, 16), rnd.randint(0, 16)) for _ in range(n)})
        if len(pts) < 3:
            continue
        cy = sum(p[0] for p in pts) / len(pts)
        cx = sum(p[1] for p in pts) / len(pts)
        pts.sort(key=lambda p: math.atan2(p[0] - cy, p[1] - cx))
        ring = [g(*p) for p in pts]
        area2 = sum(a[0] * b[1] - b[0] * a[1] for a, b in ring_edges(ring))
        if area2 != 0:
            return ring


def generate(seed):
    rnd = random.Random(seed)
    data = Data()
    scale_name, s = SCALES[seed % len(SCALES)]
    # Straddle an index tile corner near Halifax, so tile boundaries cross the data.
    clat, clon = tile_corner_near(int(44.65 * UNIT), int(-63.58 * UNIT))
    lat0, lon0 = clat - 8 * s + rnd.randint(-3, 3), clon - 8 * s + rnd.randint(-3, 3)
    data.scale = scale_name
    data.origin = (lat0, lon0)

    def g(y, x, dy=0, dx=0):
        return (lat0 + y * s + dy, lon0 + x * s + dx)

    # Polygons, each also an area (a named multipolygon relation).
    for _ in range(rnd.randint(2, 4)):
        data.polygons.append(random_ring(rnd, g))

    # Nodes: random grid points, polygon vertices, and points on or 1-2 units off edges.
    for _ in range(rnd.randint(30, 60)):
        data.node_at(g(rnd.randint(-1, 17), rnd.randint(-1, 17), rnd.randint(-2, 2), rnd.randint(-2, 2)))
    for ring in data.polygons:
        for a, b in ring_edges(ring):
            data.node_at(a)
            off = rnd.choice([-2, -1, 0, 1, 2])
            m = ((a[0] + b[0]) // 2, (a[1] + b[1]) // 2)
            data.node_at((m[0] + off, m[1]) if rnd.random() < 0.5 else (m[0], m[1] + off))

    # Ways: random polylines over existing nodes (shared endpoints) ...
    node_ids = list(data.nodes)
    wid = 0
    for _ in range(rnd.randint(10, 20)):
        wid += 1
        data.ways[wid] = rnd.sample(node_ids, rnd.randint(2, 4))
    # ... and segments parallel to polygon edges, 0-3 units off, overlapping them partly.
    for ring in data.polygons:
        for a, b in rnd.sample(ring_edges(ring), min(3, len(ring))):
            off = rnd.choice([0, 1, 2, 3]) * rnd.choice([-1, 1])
            horiz = rnd.random() < 0.5
            t0, t1 = sorted([rnd.uniform(-0.5, 1.5), rnd.uniform(-0.5, 1.5)])
            p = (round(a[0] + (b[0] - a[0]) * t0), round(a[1] + (b[1] - a[1]) * t0))
            q = (round(a[0] + (b[0] - a[0]) * t1), round(a[1] + (b[1] - a[1]) * t1))
            if horiz:
                p, q = (p[0] + off, p[1]), (q[0] + off, q[1])
            else:
                p, q = (p[0], p[1] + off), (q[0], q[1] + off)
            if p == q:
                continue
            wid += 1
            data.ways[wid] = [data.node_at(p), data.node_at(q)]
    # Axis-parallel pairs 0-2 units apart, away from any polygon.
    for _ in range(3):
        y, x0, x1 = rnd.randint(0, 16), rnd.randint(0, 8), rnd.randint(9, 16)
        d = rnd.choice([0, 1, 2])
        for dy in (0, d):
            wid += 1
            data.ways[wid] = [data.node_at(g(y, x0, dy)), data.node_at(g(y, x1, dy))]

    # Area relations: one closed way per polygon, sometimes split into two ways.
    rid = 0
    for k, ring in enumerate(data.polygons):
        ids = [data.node_at(p) for p in ring]
        rid += 1
        if len(ids) >= 4 and rnd.random() < 0.4:
            cut = rnd.randint(2, len(ids) - 2)
            parts = [ids[:cut + 1], ids[cut:] + ids[:1]]
        else:
            parts = [ids + ids[:1]]
        members = []
        for part in parts:
            wid += 1
            data.ways[wid] = part
            members.append(('way', wid, 'outer'))
        data.rels[rid] = members
        data.rel_tags[rid] = {'type': 'multipolygon', 'name': f'area{k}'}
        data.areas[rid] = ring
    # A route relation over some ways and nodes, for member recursion.
    rid += 1
    data.rels[rid] = ([('way', w, '') for w in rnd.sample(list(data.ways), 4)]
                      + [('node', n, 'stop') for n in rnd.sample(list(data.nodes), 3)])
    data.rel_tags[rid] = {'type': 'route', 'route': 'bus'}

    # Tags and metadata.
    for n in data.nodes:
        if rnd.random() < 0.3:
            data.node_tags[n] = {'amenity': rnd.choice(['cafe', 'bench'])}
    for w in data.ways:
        if rnd.random() < 0.5:
            data.way_tags.setdefault(w, {})['highway'] = rnd.choice(['residential', 'path'])
    for kind, ids in (('node', data.nodes), ('way', data.ways), ('relation', data.rels)):
        for i in ids:
            uid, user = rnd.choice(USERS)
            data.meta[(kind, i)] = (rnd.randint(1, 3), rnd.choice(TIMESTAMPS), uid, user)
    return data


# ---------------------------------------------------------------------------------------
# Queries and their expected results
#
# Each check is (name, query, expected) where expected(data) -> (must, must_not, exact):
# the elements that must be returned, those that must not, and whether the result must be
# exactly `must`.

def els(kind, ids):
    return {(kind, i) for i in ids}


def poly_str(ring):
    return ' '.join(f'{deg(lat)} {deg(lon)}' for lat, lon in ring)


def bbox_str(s, w, n, e):
    return f'{deg(s)},{deg(w)},{deg(n)},{deg(e)}'


def classify_all(items):
    must, must_not = set(), set()
    for key, c in items:
        if c == 'in':
            must.add(key)
        elif c == 'out':
            must_not.add(key)
    return must, must_not


def gen_checks(data, seed):
    rnd = random.Random(seed * 7919 + 1)
    checks = []
    nodes, ways = data.nodes, data.ways
    coords = list(nodes.values())

    def add(name, query, must, must_not=frozenset(), exact=False):
        checks.append((name, query, set(must), set(must_not), exact))

    # bbox: edges on node coordinates, 0-2 units off.
    for _ in range(6):
        ys = sorted(rnd.choice(coords)[0] + rnd.randint(-2, 2) for _ in range(2))
        xs = sorted(rnd.choice(coords)[1] + rnd.randint(-2, 2) for _ in range(2))
        s, n, w, e = ys[0], ys[1], xs[0], xs[1]
        inside = {i for i, (lat, lon) in nodes.items() if s <= lat <= n and w <= lon <= e}
        add('bbox nodes', f'node({bbox_str(s, w, n, e)});', els('node', inside), exact=True)
        rect = [(s, w), (s, e), (n, e), (n, w)]
        if n - s > 2 * MARGIN and e - w > 2 * MARGIN:
            must, must_not = classify_all(
                ((('way', i), classify_line(data.way_pts(i), rect)) for i in ways))
        else:
            must, must_not = set(), set()
        # Any way with a node in the box is in the result, whatever its size.
        must |= {('way', i) for i in ways
                 if any(s <= nodes[m][0] <= n and w <= nodes[m][1] <= e for m in ways[i])}
        add('bbox ways', f'way({bbox_str(s, w, n, e)});', must, must_not)
        tagged = {i for i in inside if data.node_tags.get(i, {}).get('amenity') == 'cafe'}
        add('bbox + tag', f'node[amenity=cafe]({bbox_str(s, w, n, e)});', els('node', tagged), exact=True)

    # poly: the generated polygons, and the same shifted by 1 unit.
    rings = list(data.polygons)
    rings += [[(lat + 1, lon) for lat, lon in r] for r in data.polygons[:2]]
    for ring in rings:
        must, must_not = classify_all(((('node', i), classify_point(p, ring)) for i, p in nodes.items()))
        add('poly nodes', f'node(poly:"{poly_str(ring)}");', must, must_not)
        must, must_not = classify_all(((('way', i), classify_line(data.way_pts(i), ring)) for i in ways))
        add('poly ways', f'way(poly:"{poly_str(ring)}");', must, must_not)
        # A polygon result lies within the polygon's bounding box (plus the margin).
        s, n = min(p[0] for p in ring) - MARGIN, max(p[0] for p in ring) + MARGIN
        w, e = min(p[1] for p in ring) - MARGIN, max(p[1] for p in ring) + MARGIN
        outside = {('node', i) for i, (lat, lon) in nodes.items() if not (s <= lat <= n and w <= lon <= e)}
        add('poly within bbox', f'node(poly:"{poly_str(ring)}");', set(), outside)

    # Areas made from the multipolygons.
    for rid, ring in data.areas.items():
        aid = AREA_ID_OFFSET + rid
        add('area exists', f'area({aid});', {('area', aid)}, exact=True)
        must, must_not = classify_all(((('node', i), classify_point(p, ring)) for i, p in nodes.items()))
        add('area nodes', f'area({aid})->.a; node(area.a);', must, must_not)
        must, must_not = classify_all(((('way', i), classify_line(data.way_pts(i), ring)) for i in ways))
        add('area ways', f'area({aid})->.a; way(area.a);', must, must_not)

    # is_in at nodes and polygon vertices.
    for p in rnd.sample(coords, 6):
        must, must_not = set(), set()
        for rid, ring in data.areas.items():
            c = classify_point(p, ring)
            if c == 'in':
                must.add(('area', AREA_ID_OFFSET + rid))
            elif c == 'out':
                must_not.add(('area', AREA_ID_OFFSET + rid))
        add('is_in', f'is_in({deg(p[0])},{deg(p[1])});', must, must_not)

    # around: radius near the distance to some node; osm3s's distance has ~0.1 m resolution.
    if data.scale in ('tile', 'wide'):
        for _ in range(4):
            c = rnd.choice(coords)
            r = round(meters(c, rnd.choice(coords)) * rnd.uniform(0.8, 1.2) + 1, 1)
            tol = 0.5 + r * 1e-3
            must = {('node', i) for i, p in nodes.items() if meters(c, p) < r - tol}
            must_not = {('node', i) for i, p in nodes.items() if meters(c, p) > r + tol}
            add('around nodes', f'node(around:{r},{deg(c[0])},{deg(c[1])});', must, must_not)
            must = {('way', i) for i in ways if meters_to_line(c, data.way_pts(i)) < r - tol}
            must_not = {('way', i) for i in ways if meters_to_line(c, data.way_pts(i)) > r + tol}
            add('around ways', f'way(around:{r},{deg(c[0])},{deg(c[1])});', must, must_not)

    # Recursion and ids.
    for w in rnd.sample(list(ways), 4):
        add('way -> nodes', f'way({w}); node(w);', els('node', ways[w]), exact=True)
    for n in rnd.sample(list(nodes), 4):
        add('node -> ways', f'node({n}); way(bn);', {('way', w) for w, nds in ways.items() if n in nds}, exact=True)
    for r, mems in data.rels.items():
        add('rel -> ways', f'relation({r}); way(r);', {('way', ref) for t, ref, _ in mems if t == 'way'}, exact=True)
    for w in rnd.sample(list(ways), 3):
        add('way -> rels', f'way({w}); rel(bw);',
            {('relation', r) for r, mems in data.rels.items() if ('way', w) in {(t, ref) for t, ref, _ in mems}},
            exact=True)
    ids = rnd.sample(list(nodes), 5)
    add('id list', f'node(id:{",".join(map(str, ids))});', els('node', ids), exact=True)

    # Set operations over two boxes.
    def box():
        ys = sorted(rnd.choice(coords)[0] for _ in range(2))
        xs = sorted(rnd.choice(coords)[1] for _ in range(2))
        return ys[0], xs[0], ys[1], xs[1]

    def in_box(b):
        return {i for i, (lat, lon) in nodes.items() if b[0] <= lat <= b[2] and b[1] <= lon <= b[3]}

    for _ in range(2):
        b1, b2 = box(), box()
        q1, q2 = f'node({bbox_str(*b1)})', f'node({bbox_str(*b2)})'
        add('union', f'({q1}; {q2};);', els('node', in_box(b1) | in_box(b2)), exact=True)
        add('difference', f'({q1}; - {q2};);', els('node', in_box(b1) - in_box(b2)), exact=True)
        add('intersection', f'{q1}->.a; {q2}->.b; node.a.b;', els('node', in_box(b1) & in_box(b2)), exact=True)

    # Metadata filters.
    uid, user = rnd.choice(USERS)
    add('uid', f'node(uid:{uid});', {('node', i) for i in nodes if data.meta[('node', i)][2] == uid}, exact=True)
    add('user', f'way(user:"{user}");', {('way', i) for i in ways if data.meta[('way', i)][3] == user}, exact=True)
    ts = rnd.choice(TIMESTAMPS[:2])
    add('newer', f'node(newer:"{ts}");', {('node', i) for i in nodes if data.meta[('node', i)][1] >= ts},
        exact=True)
    return checks


# ---------------------------------------------------------------------------------------
# Running

def import_db(bindir, data, db):
    os.makedirs(db, exist_ok=True)
    xml = data.to_xml().encode()
    p = subprocess.run([os.path.join(bindir, 'update_database'), f'--db-dir={db}/', '--meta'],
                       input=xml, capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(f'update_database failed: {p.stderr.decode(errors="replace")[-2000:]}')
    p = subprocess.run([os.path.join(bindir, 'osm3s_query'), f'--db-dir={db}/', '--rules'],
                       input=AREA_RULES, capture_output=True, timeout=120)
    if p.returncode != 0:
        raise RuntimeError(f'area rules failed: {p.stderr.decode(errors="replace")[-2000:]}')


def wrap(query):
    return f'[out:csv(::type,::id;false)][timeout:60];\n{query}\nout ids;\n'.encode()


def parse(out):
    result = set()
    for line in out.decode(errors='replace').splitlines():
        parts = line.split('\t')
        if len(parts) == 2 and parts[1].isdigit():
            result.add((parts[0], int(parts[1])))
    return result


def run_query(bindir, db, query):
    try:
        rc, out, err, _ = run_osm3s(bindir, db, wrap(query), timeout=120)
    except subprocess.TimeoutExpired:
        return -1, b'', b'timeout'
    return rc, out, err


def verify(result, must, must_not, exact):
    """Problems with one result, as a list of strings (empty if it's right)."""
    problems = []
    missing = sorted(must - result)
    extra = sorted(result & must_not)
    if exact:
        extra = sorted(result - must)
    if missing:
        problems.append('missing ' + ' '.join(f'{t}/{i}' for t, i in missing[:10]))
    if extra:
        problems.append(('unexpected ' if exact else 'clearly outside but returned ')
                        + ' '.join(f'{t}/{i}' for t, i in extra[:10]))
    return problems


def describe_failure(out_dir, seed, k, data, query, cand, base, expected, problems):
    if not out_dir:
        return None
    d = os.path.join(out_dir, f'{seed}-{k}')
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, 'data.osm'), 'w') as f:
        f.write(data.to_xml())
    with open(os.path.join(d, 'query.ql'), 'wb') as f:
        f.write(wrap(query))
    with open(os.path.join(d, 'candidate.out'), 'wb') as f:
        f.write(cand)
    if base is not None:
        with open(os.path.join(d, 'baseline.out'), 'wb') as f:
            f.write(base)
    with open(os.path.join(d, 'problems.txt'), 'w') as f:
        must, must_not, exact = expected
        f.write('\n'.join(problems) + '\n')
        f.write(f'must: {sorted(must)}\nmust not: {sorted(must_not)}\nexact: {exact}\n')
    return d


def run_seed(seed, args, tmp):
    data = generate(seed)
    checks = gen_checks(data, seed)
    db = os.path.join(tmp, f'db-{seed}')
    import_db(args.import_bin, data, db)
    results = []
    for k, (name, query, must, must_not, exact) in enumerate(checks):
        rc, out, err = run_query(args.bin, db, query)
        cand_problems = verify(parse(out), must, must_not, exact) if rc == 0 else [
            f'exit status {rc}: {err.decode(errors="replace").strip()[-300:]}']
        base_out, base_problems, differs = None, None, False
        if args.baseline:
            brc, base_out, berr = run_query(args.baseline, db, query)
            base_problems = verify(parse(base_out), must, must_not, exact) if brc == 0 else [
                f'exit status {brc}']
            differs = (brc, base_out) != (rc, out)
        failed = bool(cand_problems) or differs
        where = None
        if failed:
            where = describe_failure(args.out_dir, seed, k, data, query, out, base_out,
                                     (must, must_not, exact), cand_problems)
        results.append((name, query, cand_problems, base_problems, differs, where))
    shutil.rmtree(db, ignore_errors=True)
    return seed, data.scale, results


def parse_seeds(text):
    seeds = []
    for part in text.split(','):
        if '-' in part:
            a, b = part.split('-')
            seeds.extend(range(int(a), int(b) + 1))
        else:
            seeds.append(int(part))
    return seeds


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--bin', help='directory with osm3s_query (and update_database) of the build under test')
    ap.add_argument('--baseline', help='directory of the baseline build; outputs must match it byte for byte')
    ap.add_argument('--import-bin', help='build whose update_database creates the databases '
                    '(default: --baseline if given, else --bin), so both builds query the same files')
    ap.add_argument('--seeds', default='1-40', help='seeds, e.g. 1-40 or 3,7,9 (default 1-40)')
    ap.add_argument('--jobs', type=int, default=os.cpu_count() or 1)
    ap.add_argument('--out-dir', help='write each failure to OUT_DIR/<seed>-<check>/')
    ap.add_argument('--show', action='store_true', help='print the data and queries of the seeds and exit')
    ap.add_argument('--verbose', action='store_true', help='print every check')
    args = ap.parse_args()
    seeds = parse_seeds(args.seeds)

    if args.show:
        for seed in seeds:
            data = generate(seed)
            print(f'# seed {seed}: scale {data.scale}, origin {deg(data.origin[0])} {deg(data.origin[1])}')
            print(data.to_xml())
            for name, query, must, must_not, exact in gen_checks(data, seed):
                print(f'# {name}: must {len(must)}, must not {len(must_not)}, exact {exact}')
                print(query)
        return 0

    if not args.bin:
        ap.error('--bin is required')
    args.import_bin = args.import_bin or args.baseline or args.bin
    for b in filter(None, [args.bin, args.baseline]):
        if not os.path.exists(os.path.join(b, 'osm3s_query')):
            ap.error(f'{b}: no osm3s_query')
    if not os.path.exists(os.path.join(args.import_bin, 'update_database')):
        ap.error(f'{args.import_bin}: no update_database (use --import-bin)')

    gen_hash = hashlib.sha256(open(__file__, 'rb').read()).hexdigest()[:12]
    print(f'props.py {gen_hash}: seeds {args.seeds}, candidate {args.bin}'
          + (f', baseline {args.baseline}' if args.baseline else '') + f', imports by {args.import_bin}')
    start = time.time()
    stats = {}   # name -> [checks, candidate failures, of those also failing on baseline, differ]
    new_failures = 0
    tmp = tempfile.mkdtemp(prefix='overpass-props-')
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
            futures = [pool.submit(run_seed, seed, args, tmp) for seed in seeds]
            for fut in concurrent.futures.as_completed(futures):
                seed, scale, results = fut.result()
                for name, query, cand, base, differs, where in results:
                    st = stats.setdefault(name, [0, 0, 0, 0])
                    st[0] += 1
                    if cand:
                        st[1] += 1
                        if base:
                            st[2] += 1
                    if differs:
                        st[3] += 1
                    new = (cand and not base) or differs
                    if new:
                        new_failures += 1
                    if new or (args.verbose and cand):
                        label = 'FAIL' if new else 'also on baseline'
                        print(f'{label}: seed {seed} ({scale}) {name}: {query}')
                        for p in cand:
                            print(f'    {p}')
                        if differs:
                            print('    output differs from the baseline')
                        if where:
                            print(f'    written to {where}')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    print(f'{"check":<20} {"runs":>6} {"fail":>6} {"baseline too":>12} {"differ":>6}')
    for name, (n, f, fb, d) in sorted(stats.items()):
        print(f'{name:<20} {n:>6} {f:>6} {fb if args.baseline else "-":>12} {d if args.baseline else "-":>6}')
    print(f'{len(seeds)} seeds in {time.time() - start:.1f}s; '
          + ('no new failures' if not new_failures else f'{new_failures} new failures'))
    return 1 if new_failures else 0


if __name__ == '__main__':
    sys.exit(main())
