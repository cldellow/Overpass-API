"""Statement forms for fuzz.py's `statements` feature, which the other features don't generate:
`local` (all three types), global tag queries with every tag-filter form (regex values, regex
keys, case-insensitive, alternations, negations), area filters by id, polyline `around`, recursion
on `_` and with roles, and the `foreach` syntax variants. All of them run on the plain database;
area filters by id need the `areas` feature as well."""


def global_tag_query(gen):
    """A tag-only query: no bbox or id, so the global tag index answers it. A rare tag keeps the
    result small; the extra filters cover the other tag-filter forms."""
    r = gen.r
    kind = r.choice(['node', 'way', 'rel', 'nwr', 'nw', 'wr'])
    key, value = gen.pick(gen.v['rare_tags'])
    first = r.choice([
        f'[{gen.quote(key)}={gen.quote(value)}]',
        f'[{gen.quote(key)}~{gen.quote("^" + value + "$")}]',
        f'[{gen.quote(key)}~{gen.quote("^" + value.lower() + "$")},i]',
        f'[~{gen.quote("^" + key + "$")}~{gen.quote("^" + value + "$")}]'])
    extra = []
    for _ in range(r.randint(0, 2)):
        k2, v2 = gen.tag()
        extra.append(r.choice([
            f'[{gen.quote(k2)}]', f'[!{gen.quote(k2)}]', f'[{gen.quote(k2)}!={gen.quote(v2)}]',
            f'[{gen.quote(k2)}~{gen.quote(".")}]', f'[{gen.quote(k2)}!~{gen.quote("^" + v2[:2])}]',
            f'[{gen.quote(k2)}~{gen.quote(v2[:3])},i]', f'[~{gen.quote("^" + k2[:3])}~{gen.quote(".")}]',
            f'[~{gen.quote(k2[:4])}~{gen.quote(v2[:2])},i]']))
    return f'{kind}{first}{"".join(extra)}->.{gen.new_set("any")};'


def polyline_around(gen):
    r = gen.r
    points = []
    lat, lon = gen.pick(gen.v['node_coords'])
    for _ in range(r.randint(2, 6)):
        points.append(f'{lat:.6f},{lon:.6f}')
        lat += r.uniform(-0.003, 0.003)
        lon += r.uniform(-0.004, 0.004)
    target = r.choice(['node', 'way', 'nwr'])
    key, value = gen.tag()
    return (f'{target}(around:{r.choice([0, 10, 50, 200])},{",".join(points)})'
            f'[{gen.quote(key)}]->.{gen.new_set(target)};')


def area_by_id(gen):
    ids = gen.v.get('area_relation_ids')
    if not ids:
        return None
    area_id = 3600000000 + gen.pick(ids)
    target = gen.r.choice(['node', 'way', 'nwr'])
    key, value = gen.pick(gen.v['rare_tags'])
    return f'{target}(area:{area_id})[{gen.quote(key)}]->.{gen.new_set(target)};'


def statement(gen, name, kind):
    """One statement reading set `name`, defining a new set; or None."""
    r = gen.r
    pick = r.choice(['local', 'local', 'global', 'global', 'around_line', 'area_id', 'recurse_default',
                     'recurse_role', 'foreach'])
    if pick == 'local':
        typ = r.choice(['', ' ll', ' llb'])
        inner = gen.new_set('any')
        out = gen.new_set('derived')
        # local needs the members' geometry, so complete the set first.
        return f'(.{name}; .{name} >;)->.{inner};\n.{inner} local{typ}->.{out};'
    if pick == 'global':
        return global_tag_query(gen)
    if pick == 'around_line':
        return polyline_around(gen)
    if pick == 'area_id' and 'areas' in gen.features:
        stmt = area_by_id(gen)
        if stmt:
            return stmt
    if pick == 'recurse_default':
        # Recursion and member filters on the default set _.
        form = r.choice(['>;', '>>;', '<;', '<<;', 'node(w);', 'way(r);', 'rel(bn);', 'rel(bw);',
                         'node(r);', 'rel(br);', 'way(bn);'])
        out = gen.new_set('any')
        return f'.{name}->._; {form} ._->.{out};'.replace('._->', '(._;)->')
    if pick == 'recurse_role':
        role = gen.quote(gen.pick(gen.v['roles'] or ['outer']))
        form = r.choice([f'node(r.{name}:{role})', f'way(r.{name}:{role})', f'rel(r.{name}:{role})',
                         f'rel(bn.{name}:{role})', f'rel(bw.{name}:{role})', f'rel(br.{name}:{role})',
                         f'way(r.{name}:"")'])
        return f'{form}->.{gen.new_set("any")};'
    # foreach variants: input and loop variable in both positions, and the default ones.
    out = gen.new_set('any')
    body = r.choice(['(._; >;);', '._ out ids;', 'node(w)->.n; .n out count;', '(._; <;);'])
    form = r.choice([f'foreach.{name}->.x({body.replace("._", ".x")})', f'foreach.{name}({body})',
                     f'.{name}->._; foreach({body})', f'.{name}->._; foreach->.x({body.replace("._", ".x")})'])
    return f'{form}\n(._;)->.{out};'


def compare_statement(gen):
    """compare, for queries in diff mode: plain, with a delta evaluator, and with a block."""
    r = gen.r
    name, _ = gen.existing_set()
    out = gen.new_set('any')
    key = gen.quote(gen.pick(gen.v['keys'][:60]))
    delta = r.choice(['', f'(delta:t[{key}])', '(delta:version())', '(delta:lat())',
                      f'(delta:is_tag({key}))', '(delta:count_tags())'])
    # The evaluator goes before the output set: .s compare(delta:e)->.t (not after, as the
    # statement's documentation says).
    head = (f'.{name} compare{delta}->.{out}' if delta else
            r.choice([f'.{name} compare()->.{out}', f'.{name} compare->.{out}']))
    if r.random() < 0.4:
        inner = r.choice([f'(.{out}; .{out} >;)->.{out};', f'.{out} out count;',
                          f'node(w.{out})->.{out}_n; .{out}_n out ids;'])
        return f'{head} {{ {inner} }}'
    return head + ';'
