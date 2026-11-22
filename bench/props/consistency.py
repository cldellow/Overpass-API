"""Consistency checks for props.py: pairs of queries that must agree.

Unlike the other checks, these need no expected answer, so they also catch bugs that the
baseline shares. Each check is (name, query_a, query_b, relation[, ignore]), relation being
'equal' (same elements) or 'subset' (a's elements are all in b's), and ignore a set of
elements left out of the comparison.

Comparisons of regions ignore elements that come within one unit of the region's boundary
without being clearly inside: nodes strictly less than one unit from a sloped edge, and ways
within one unit of any edge with no node or segment point at least one unit inside. The
geometry tests round crossing points by integer division from one end of a segment, so for
such elements the result can depend on vertex order and on where tile boundaries cut the
segments. Elements exactly one unit away, or clearly crossing, are compared.

Translation checks compare the same query on the seed's data and on a copy shifted by an
offset that doesn't line up with index tiles, so tile boundaries fall elsewhere in the data.
All coordinates are integers, so the shift is exact and the results must have the same ids.
"""

import copy
import random

import props
from props import deg, bbox_str, poly_str, TILE

# Shift for the translation checks: not a multiple of the tile size in either direction.
SHIFT = (3 * TILE + 12345, -(5 * TILE) + 27183)


def clearly_inside(pts, ring):
    """Some node or segment point of the polyline at least one unit inside the ring."""
    edges = props.ring_edges(ring)

    def deep(q):
        return props.point_in_ring(q, ring) and min(props.seg_point_dist(q, a, b) for a, b in edges) >= 1
    if any(deep(p) for p in pts):
        return True
    for a, b in zip(pts, pts[1:]):
        n = min(400, int(max(abs(b[0] - a[0]), abs(b[1] - a[1]))) * 2 + 1)
        if any(deep((a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n)) for k in range(1, n)):
            return True
    return False


def near_sloped_edges(data, ring, *more):
    """Elements whose result for this region (and the rings in more) may depend on rounding:
    nodes strictly less than one unit from a sloped edge; ways within one unit of any edge
    and not clearly inside."""
    found = set()
    for r in (ring,) + more:
        edges = props.ring_edges(r)
        sloped = [(a, b) for a, b in edges if a[0] != b[0] and a[1] != b[1]]
        if sloped:
            found |= {('node', i) for i, p in data.nodes.items()
                      if min(props.seg_point_dist(p, a, b) for a, b in sloped) < 1}
        for i in data.ways:
            pts = data.way_pts(i)
            segs = list(zip(pts, pts[1:])) or [(pts[0], pts[0])]
            if (min(props.seg_seg_dist(x, y, a, b) for x, y in segs for a, b in edges) < 1
                    and not clearly_inside(pts, r)):
                found.add(('way', i))
    return found


def near_sloped_edges_point(p, ring):
    edges = [(a, b) for a, b in props.ring_edges(ring) if a[0] != b[0] and a[1] != b[1]]
    return {('node', -1)} if edges and min(props.seg_point_dist(p, a, b) for a, b in edges) < 1 else set()


def rect_ring(s, w, n, e):
    return [(s, w), (s, e), (n, e), (n, w)]


def gen_consistency_checks(data, seed):
    rnd = random.Random(seed * 3571 + 13)
    checks = []
    special = getattr(data, 'special', set())
    plain = [i for i in data.nodes if i not in special]
    coords = [data.nodes[i] for i in plain]
    lat_tile = lambda v: round((v + 91 * props.UNIT) / TILE) * TILE - 91 * props.UNIT

    def add(name, a, b, relation='equal'):
        checks.append((name, a, b, relation))

    def box():
        ys = sorted(rnd.choice(coords)[0] + rnd.randint(-2, 2) for _ in range(2))
        xs = sorted(rnd.choice(coords)[1] + rnd.randint(-2, 2) for _ in range(2))
        return ys[0], xs[0], ys[1], xs[1]

    # Split a box in two (at a tile boundary when one crosses it): the halves' union is the box.
    for _ in range(4):
        s, w, n, e = box()
        if n - s < 2 or e - w < 2:
            continue
        if rnd.random() < 0.5:
            m = lat_tile((s + n) // 2)
            m = m if s < m < n else rnd.randint(s + 1, n - 1)
            halves = [(s, w, m, e), (m, w, n, e)]
        else:
            m = round(((w + e) // 2) / TILE) * TILE
            m = m if w < m < e else rnd.randint(w + 1, e - 1)
            halves = [(s, w, n, m), (s, m, n, e)]
        ring = rect_ring(s, w, n, e)
        near = near_sloped_edges(data, ring, rect_ring(*halves[0]), rect_ring(*halves[1]))
        for kind in ('node', 'way'):
            checks.append((f'split bbox {kind}s', f'{kind}({bbox_str(s, w, n, e)});',
                           f'({kind}({bbox_str(*halves[0])}); {kind}({bbox_str(*halves[1])}););', 'equal', near))
        # The same rectangle as a polygon, split the same way.
        for kind in ('node', 'way'):
            checks.append((f'split poly {kind}s', f'{kind}(poly:"{poly_str(ring)}");',
                           f'({kind}(poly:"{poly_str(rect_ring(*halves[0]))}"); {kind}(poly:"{poly_str(rect_ring(*halves[1]))}"););',
                           'equal', near))
        # The ways of the nodes in a box are all in way(bbox).
        add('bbox: ways of nodes within way(bbox)', f'node({bbox_str(s, w, n, e)}); way(bn);',
            f'way({bbox_str(s, w, n, e)});', 'subset')
        # [bbox:] as a setting, and the bbox inline.
        add('[bbox:] setting = inline bbox', f'[bbox:{bbox_str(s, w, n, e)}]; node[amenity];',
            f'node[amenity]({bbox_str(s, w, n, e)});')

    # A polygon reversed, and starting at another vertex.
    for ring in data.polygons:
        k = rnd.randint(1, len(ring) - 1)
        near = near_sloped_edges(data, ring)
        for kind in ('node', 'way'):
            checks.append((f'poly reversed {kind}s', f'{kind}(poly:"{poly_str(ring)}");',
                           f'{kind}(poly:"{poly_str(list(reversed(ring)))}");', 'equal', near))
            checks.append((f'poly rotated {kind}s', f'{kind}(poly:"{poly_str(ring)}");',
                           f'{kind}(poly:"{poly_str(ring[k:] + ring[:k])}");', 'equal', near))

    # Filter order (metadata filters, tag filters, geometry).
    uid = rnd.choice(props.USERS)[0]
    s, w, n, e = box()
    b = bbox_str(s, w, n, e)
    forms = [f'node[amenity]({b})(uid:{uid});', f'node(uid:{uid})[amenity]({b});',
             f'node({b})(uid:{uid})[amenity];', f'node({b})->.x; node.x[amenity](uid:{uid});']
    for f in forms[1:]:
        add('filter order', forms[0], f)
    ring = rnd.choice(data.polygons)
    add('filter order (poly)', f'way[highway](poly:"{poly_str(ring)}");', f'way(poly:"{poly_str(ring)}")[highway];')

    # around a point = around a node at that point; a way is within 0 m of itself.
    if data.scale in ('tile', 'wide'):
        for _ in range(2):
            i = rnd.choice(plain)
            p = data.nodes[i]
            r = rnd.choice([5, 50, 500])
            for kind in ('node', 'way'):
                add(f'around point = around node ({kind}s)', f'{kind}(around:{r},{deg(p[0])},{deg(p[1])});',
                    f'node({i})->.c; {kind}(around.c:{r});')
        wid = rnd.choice(list(data.ways))
        add('way within around.self:0', f'way({wid});', f'way({wid})->.s; way(around.s:0);', 'subset')
    return checks


def shifted(data, dy=SHIFT[0], dx=SHIFT[1]):
    """A copy of data with every coordinate moved by (dy, dx) units."""
    d = copy.deepcopy(data)
    special = getattr(data, 'special', set())
    d.nodes = {i: ((p[0] + dy, p[1] + dx) if i not in special else p) for i, p in data.nodes.items()}
    d.polygons = [[(p[0] + dy, p[1] + dx) for p in r] for r in data.polygons]
    d.areas = {k: [(p[0] + dy, p[1] + dx) for p in r] for k, r in data.areas.items()}
    d.origin = (data.origin[0] + dy, data.origin[1] + dx)
    return d


def gen_translation_checks(data, seed):
    """(name, query on the original data, the same query on the shifted data, ignore)."""
    rnd = random.Random(seed * 2953 + 17)
    special = getattr(data, 'special', set())
    coords = [p for i, p in data.nodes.items() if i not in special]
    dy, dx = SHIFT
    checks = []

    def mv(s, w, n, e):
        return s + dy, w + dx, n + dy, e + dx

    for _ in range(4):
        ys = sorted(rnd.choice(coords)[0] + rnd.randint(-2, 2) for _ in range(2))
        xs = sorted(rnd.choice(coords)[1] + rnd.randint(-2, 2) for _ in range(2))
        bb = (ys[0], xs[0], ys[1], xs[1])
        near = near_sloped_edges(data, rect_ring(*bb))
        for kind in ('node', 'way'):
            checks.append((f'shifted bbox {kind}s', f'{kind}({bbox_str(*bb)});', f'{kind}({bbox_str(*mv(*bb))});', near))
    for ring in data.polygons:
        moved = [(p[0] + dy, p[1] + dx) for p in ring]
        near = near_sloped_edges(data, ring)
        for kind in ('node', 'way'):
            checks.append((f'shifted poly {kind}s', f'{kind}(poly:"{poly_str(ring)}");', f'{kind}(poly:"{poly_str(moved)}");', near))
    for rid, ring in data.areas.items():
        aid = props.AREA_ID_OFFSET + rid
        near = near_sloped_edges(data, ring)
        for kind in ('node', 'way'):
            checks.append((f'shifted area {kind}s', f'area({aid})->.a; {kind}(area.a);', f'area({aid})->.a; {kind}(area.a);', near))
    for p in rnd.sample(coords, 4):
        near = {('area', props.AREA_ID_OFFSET + rid) for rid, ring in data.areas.items()
                if (('node', -1) in near_sloped_edges_point(p, ring))}
        checks.append(('shifted is_in', f'is_in({deg(p[0])},{deg(p[1])});', f'is_in({deg(p[0] + dy)},{deg(p[1] + dx)});', near))
    return checks
