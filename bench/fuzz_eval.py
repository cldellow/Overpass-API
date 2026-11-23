"""Evaluator statements for fuzz.py's `evaluators` feature: convert, make, for, if/else, and
richer (if:) filters, built from a small typed expression grammar.

Types: N number, S string, B boolean (a number, 0 or 1), G geometry, L list (strings joined
by ";"). Element expressions run per element (convert, (if:), for); set expressions run once per
set (make, if). The functions are the ones the parser registers (stmt_func_name and
maker_by_func_name in src/overpass_api/statements); each one gets generated in the right type
and context, so most queries parse, and a few deliberately don't (bad arity, wrong types), to
reach the error paths."""


class Expr:
    def __init__(self, gen, depth=3):
        self.g = gen          # the fuzz.py Generator: rng, vocabulary, quoting
        self.r = gen.r
        self.depth = depth

    # ----- leaves ------------------------------------------------------------

    def key(self):
        return self.g.quote(self.g.pick(self.g.v['keys'][:60]))

    def value(self):
        return self.g.quote(self.g.tag()[1])

    def role(self):
        roles = self.g.v.get('roles') or ['outer']
        return self.g.quote(self.g.pick(roles))

    def num_literal(self):
        return self.r.choice(['0', '1', '2', '3', '10', '100', '0.5', '-1', '1e3', '42'])

    def str_literal(self):
        return self.r.choice(['""', '"a"', '"yes"', '"1;2;3"', '"2018-01-01"', '"x;y"', self.value()])

    # ----- element expressions -----------------------------------------------

    def elem(self, typ, d=None):
        d = self.depth if d is None else d
        if d <= 0 or self.r.random() < 0.3:
            return self.elem_leaf(typ)
        return self.elem_node(typ, d - 1)

    def elem_leaf(self, typ):
        if typ == 'N':
            return self.r.choice([
                'id()', 'version()', 'uid()', 'changeset()', 'count_tags()', 'count_members()',
                'count_distinct_members()', f'count_by_role({self.role()})',
                f'count_distinct_by_role({self.role()})', 'lat()', 'lon()', 'length()',
                f'number(t[{self.key()}])', self.num_literal()])
        if typ == 'S':
            return self.r.choice([
                f't[{self.key()}]', 'type()', 'user()', 'timestamp()',
                f'suffix(t[{self.key()}])', self.str_literal(), 'id()'])
        if typ == 'B':
            return self.r.choice([
                f'is_tag({self.key()})', 'is_closed()', f'is_number(t[{self.key()}])',
                f'is_date(t[{self.key()}])', f't[{self.key()}]=={self.value()}', '1', '0'])
        if typ == 'G':
            lat, lon = self.g.pick(self.g.v['node_coords'])
            return self.r.choice([
                'geom()', 'center(geom())', 'hull(geom())', 'trace(geom())', f'pt({lat:.6f},{lon:.6f})',
                f'lstr(pt({lat:.6f},{lon:.6f}),pt({lat + 0.001:.6f},{lon + 0.002:.6f}))',
                f'poly(lstr(pt({lat:.6f},{lon:.6f}),pt({lat + 0.001:.6f},{lon:.6f}),'
                f'pt({lat + 0.001:.6f},{lon + 0.001:.6f}),pt({lat:.6f},{lon:.6f})))'])
        if typ == 'L':
            return self.r.choice([f't[{self.key()}]', '"a;b;c"', '"1;2"', f'lrs_union(t[{self.key()}],"name")'])
        raise ValueError(typ)

    def elem_node(self, typ, d):
        e = lambda t: self.elem(t, d)
        if typ == 'N':
            forms = [lambda: f'({e("N")}{self.r.choice(["+", "-", "*", "/"])}{e("N")})',
                     lambda: f'(-{e("N")})', lambda: f'abs({e("N")})', lambda: f'number({e("S")})',
                     lambda: f'date({e("S")})', lambda: f'({e("B")}?({e("N")}):({e("N")}))',
                     lambda: f'lrs_max({e("L")})', lambda: f'lrs_min({e("L")})']
        elif typ == 'S':
            forms = [lambda: f'suffix({e("S")})', lambda: f'lrs_min({e("L")})',
                     lambda: f'({e("B")}?({e("S")}):({e("S")}))', lambda: f'({e("S")}+{e("S")})',
                     lambda: e('N')]
        elif typ == 'B':
            forms = [lambda: f'({e("N")}{self.r.choice(["<", "<=", ">", ">=", "==", "!="])}{e("N")})',
                     lambda: f'({e("S")}{self.r.choice(["==", "!=", "<", ">"])}{e("S")})',
                     lambda: f'({e("B")}{self.r.choice(["&&", "||"])}{e("B")})',
                     lambda: f'(!{e("B")})', lambda: f'is_number({e("S")})', lambda: f'is_date({e("S")})',
                     lambda: f'lrs_in({e("S")},{e("L")})']
        elif typ == 'G':
            forms = [lambda: f'center({e("G")})', lambda: 'geom()', lambda: self.elem_leaf('G')]
        elif typ == 'L':
            forms = [lambda: f'lrs_union({e("L")},{e("L")})', lambda: f'lrs_isect({e("L")},{e("L")})',
                     lambda: e('S')]
        else:
            raise ValueError(typ)
        return self.r.choice(forms)()

    def member_expr(self):
        """per_member over a way's or relation's members, with the member functions."""
        inner = self.r.choice(['pos()', 'mtype()', 'ref()', 'role()', f'(pos()>{self.r.randint(0, 3)})',
                               f'(role()=={self.role()})', 'mtype()+"/"+ref()'])
        return f'per_member({inner})'

    def vertex_expr(self):
        return self.r.choice(['per_vertex(angle())', 'per_vertex(pos())', 'per_vertex(angle()>90)'])

    # ----- set expressions -----------------------------------------------------

    def set_expr(self, typ, prefix='', d=2):
        """A set-level expression: aggregates combined with operators, ternaries and string
        functions. prefix is "s." to name the set instead of the default _."""
        if d > 0 and self.r.random() < 0.45:
            e = lambda t: self.set_expr(t, prefix, d - 1)
            if typ == 'N':
                return self.r.choice([
                    lambda: f'({e("N")}{self.r.choice(["+", "-", "*", "/"])}{e("N")})',
                    lambda: f'(-{e("N")})', lambda: f'abs({e("N")})', lambda: f'number({e("S")})',
                    lambda: f'date({prefix}max(timestamp()))', lambda: f'({e("B")}?({e("N")}):({e("N")}))'])()
            if typ == 'S':
                return self.r.choice([
                    lambda: f'({e("S")}+{e("S")})', lambda: f'suffix({e("S")})', lambda: f'lrs_min({e("S")})',
                    lambda: f'lrs_union({e("S")},{e("S")})', lambda: f'({e("B")}?({e("S")}):({e("S")}))'])()
            if typ == 'B':
                return self.r.choice([
                    lambda: f'({e("N")}{self.r.choice(["<", "<=", ">", ">=", "==", "!="])}{e("N")})',
                    lambda: f'({e("S")}{self.r.choice(["==", "!=", "<"])}{e("S")})',
                    lambda: f'({e("B")}{self.r.choice(["&&", "||"])}{e("B")})', lambda: f'(!{e("B")})',
                    lambda: f'is_number({e("S")})', lambda: f'lrs_in({self.str_literal()},{e("S")})'])()
            if typ == 'G':
                return f'center({self.set_expr("G", prefix, 0)})'
        return self.set_leaf(typ, prefix)

    def set_leaf(self, typ, prefix=''):
        if typ == 'N':
            return self.r.choice([
                f'{prefix}count({self.r.choice(["nodes", "ways", "relations", "deriveds", "nwr", "nw", "wr", "nr"])})',
                f'{prefix}sum({self.elem("N", 1)})', f'{prefix}min({self.elem("N", 1)})',
                f'{prefix}max({self.elem("N", 1)})', f'number({prefix}u({self.elem("S", 1)}))'])
        if typ == 'S':
            return self.r.choice([f'{prefix}u({self.elem("S", 1)})', f'{prefix}set({self.elem("S", 1)})',
                                  f'{prefix}min({self.elem("S", 1)})', f'{prefix}max({self.elem("S", 1)})'])
        if typ == 'B':
            return f'({self.set_leaf("N", prefix)}{self.r.choice([">", "<", "==", ">="])}{self.num_literal()})'
        if typ == 'G':
            return self.r.choice([f'trace({prefix}gcat(geom()))', f'hull({prefix}gcat(geom()))',
                                  f'{prefix}gcat(geom())', f'{prefix}gcat(center(geom()))'])
        raise ValueError(typ)


def broken_expr(gen):
    """A malformed expression, for the parser's and evaluators' error paths."""
    return gen.r.choice(['count_tags(1)', 't[]', 'id(', 'number()', 'lrs_in("a")', 'pt(1)', 'u()',
                         '1 +', 'is_tag()', 'per_member()', 'nosuchfunc()', '"a" ? 1', 'date(1,2)'])


def evaluator_statement(gen, name, kind):
    """One evaluator statement reading set `name`, defining a new set; or None."""
    x = Expr(gen)
    r = gen.r
    pick = r.choice(['convert', 'convert', 'make', 'make_set', 'for', 'if', 'filter', 'filter'])
    if pick == 'convert':
        pairs = []
        if r.random() < 0.5:
            pairs.append('::id=id()')
        if r.random() < 0.3:
            pairs.append('::=::')
        for i in range(r.randint(1, 4)):
            typ = r.choice(['N', 'S', 'B', 'L', 'S'])
            pairs.append(f'k{i}={x.elem(typ)}')
        if r.random() < 0.25:
            pairs.append(f'!{gen.quote(gen.pick(gen.v["keys"][:60]))}')
        if kind in ('way', 'rel', 'any') and r.random() < 0.3:
            pairs.append(f'members={x.member_expr()}')
        if kind in ('way', 'any') and r.random() < 0.15:
            pairs.append(f'angles={x.vertex_expr()}')
        if r.random() < 0.3:
            pairs.append(f'::geom={x.elem("G")}')
        if r.random() < 0.03:
            pairs.append(f'bad={broken_expr(gen)}')
        r.shuffle(pairs)
        return f'.{name} convert {r.choice(["item", "row", "stat"])} {",".join(pairs)}->.{gen.new_set("derived")};'
    if pick == 'make':
        # make reads no input set: aggregates name their set (s.count(...)), or use _.
        prefix = name + '.'
        pairs = [f'k{i}={x.set_expr(r.choice(["N", "S", "N"]), prefix)}' for i in range(r.randint(1, 4))]
        if r.random() < 0.3:
            pairs.append(f'::geom={x.set_expr("G", prefix)}')
        if r.random() < 0.03:
            pairs.append(f'bad={broken_expr(gen)}')
        return f'make stat {",".join(pairs)}->.{gen.new_set("derived")};'
    if pick == 'make_set':
        # make without an input set, reading other sets by name.
        pairs = ['n=1', f'c={x.set_expr("N", name + ".")}', f'v={x.set_expr("S", name + ".")}']
        return f'make info {",".join(r.sample(pairs, r.randint(1, 3)))}->.{gen.new_set("derived")};'
    if pick == 'for':
        group = 'keys()' if r.random() < 0.2 else x.elem(r.choice(['S', 'N', 'B']), 2)
        body = r.choice(['make group value=_.val,count=count(nwr);', '._ out count;',
                         'make group value=_.val,n=count(nodes),w=count(ways);', 'convert item ::id=id(),g=_.val;'])
        out = gen.new_set('derived')
        return f'for.{name}->.{out} ({group}) {{ {body} }}'
    if pick == 'if':
        cond = x.set_expr('B', name + '.')
        out = gen.new_set('derived')
        then = f'make yes n={name}.count(nwr)->.{out};'
        other = f'make no n=0->.{out};'
        if r.random() < 0.3:
            return f'if ({cond}) {{ {then} }}'
        return f'if ({cond}) {{ {then} }} else {{ {other} }}'
    # filter
    target = kind if kind in ('node', 'way', 'rel') else r.choice(['node', 'way', 'rel', 'nwr'])
    cond = x.elem('B') if r.random() < 0.97 else broken_expr(gen)
    return f'{target}.{name}(if:{cond})->.{gen.new_set(target)};'
