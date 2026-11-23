"""Output formats and settings for fuzz.py's `output` feature: [out:xml|json|csv(...)|popup(...)]
headers with every CSV field, header line and separator option, every `out` mode, geometry,
order, `noids`, limit and bbox, and [bbox:], [maxsize:] and [timeout:] settings.

The fields, modes and options are the ones the parser and the output handlers accept
(map_ql_parser.cc parse_output, output_formats/*_factory.cc and output_csv.cc). [out:custom] is
left out: it needs templates in the database directory."""

# Field names with a colon must be quoted after the "::".
CSV_FIELDS = ['::id', '::type', '::otype', '::lat', '::lon', '::version', '::timestamp',
              '::changeset', '::uid', '::user', '::count', '::"count:nodes"', '::"count:ways"',
              '::"count:relations"', '::"count:areas"']


def csv_header(gen):
    r = gen.r
    fields = r.sample(CSV_FIELDS, r.randint(1, 6))
    for _ in range(r.randint(0, 3)):
        fields.append(gen.quote(gen.pick(gen.v['keys'][:80])))
    if r.random() < 0.1:
        fields.append('"name:en"')
    r.shuffle(fields)
    spec = ','.join(fields)
    if r.random() < 0.5:
        spec += ';' + r.choice(['true', 'false'])
        if r.random() < 0.6:
            spec += ';' + gen.quote(r.choice([',', '|', ';', ' ', '\t', '::']))
    return f'[out:csv({spec})]'


def popup_header(gen):
    """[out:popup] with categories: a title, tag filter conjunctions and an optional title key."""
    r = gen.r
    categories = []
    for _ in range(r.randint(1, 3)):
        parts = [gen.quote(r.choice(['Shops', 'Food', 'Roads', 'Other', 'Named things']))]
        for _ in range(r.randint(1, 3)):
            conj = []
            for _ in range(r.randint(1, 2)):
                key, value = gen.tag()
                form = r.random()
                if form < 0.4:
                    conj.append(f'[{gen.quote(key)}={gen.quote(value)}]')
                elif form < 0.6:
                    conj.append(f'[{gen.quote(key)}]')
                elif form < 0.75:
                    conj.append(f'[{gen.quote(key)}!={gen.quote(value)}]')
                elif form < 0.9:
                    conj.append(f'[{gen.quote(key)}~{gen.quote("^" + value[:2])}]')
                else:
                    # Popup filters have no [!key]; negation is != or !~.
                    conj.append(f'[{gen.quote(key)}!~{gen.quote("^" + value[:2])}]')
            parts.append(''.join(conj))
        if r.random() < 0.6:
            parts.append(gen.quote(r.choice(['name', 'ref', 'highway', 'amenity'])))
        categories.append('(' + ';'.join(parts) + ';)')
    return f'[out:popup{"".join(categories)}]'


def header(gen):
    """The settings line: an output format and some settings."""
    r = gen.r
    fmt = r.random()
    if fmt < 0.2:
        h = '[out:xml]'
    elif fmt < 0.45:
        h = '[out:json]'
    elif fmt < 0.8:
        h = csv_header(gen)
    elif fmt < 0.92:
        h = popup_header(gen)
    else:
        h = ''
    settings = []
    if r.random() < 0.7:
        settings.append(f'[timeout:{r.choice([25, 60, 180])}]')
    if r.random() < 0.7:
        settings.append(f'[maxsize:{r.choice([536870912, 1073741824, 268435456])}]')
    if r.random() < 0.2:
        settings.append('[bbox:' + gen.bbox(0.3)[1:-1] + ']')
    settings.append(h)
    r.shuffle(settings)
    return ''.join(settings) or '[out:xml]'


def out_statement(gen, name):
    r = gen.r
    tokens = []
    if r.random() < 0.01:
        # The parser accepts quirks, but the print statement rejects it: an error path only.
        tokens.append('quirks')
    elif r.random() < 0.85:
        tokens.append(r.choice(['ids', 'skel', 'body', 'tags', 'meta', 'count', 'body', 'meta']))
    if r.random() < 0.45:
        tokens.append(r.choice(['geom', 'bb', 'center']))
    if r.random() < 0.3:
        tokens.append(r.choice(['qt', 'asc']))
    if r.random() < 0.15:
        tokens.append('noids')
    if r.random() < 0.2:
        tokens.append(str(r.choice([1, 2, 5, 17, 100])))
    if r.random() < 0.15:
        tokens.append(gen.bbox(0.1))
    r.shuffle(tokens)
    prefix = f'.{name} ' if r.random() < 0.9 else ''
    return f'{prefix}out {" ".join(tokens)};'.replace('out ;', 'out;')
