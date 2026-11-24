#!/usr/bin/env python3
"""Tests for fuzz.py's query generator: coverage of the constructs it should produce, and,
given an Overpass build, that the generated queries parse.

  python3 bench/test_fuzz.py                      # coverage and the baseline cache, no Overpass needed
  python3 bench/test_fuzz.py --bin BUILD/bin      # also parse every query (osm3s_query --dump-xml)

Uses a small synthetic vocabulary, so it needs no extract or database. Exit status is 1
if any check fails.
"""
import argparse
import importlib.util
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('fuzz', os.path.join(HERE, 'fuzz.py'))
fuzz = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fuzz)

# A synthetic vocabulary shaped like fuzz_vocab.py's output. Values are made up.
VOCAB = {
    'common_tags': [['amenity', 'cafe'], ['highway', 'residential'], ['building', 'yes'],
                    ['shop', 'bakery'], ['natural', 'tree'], ['landuse', 'grass'],
                    ['highway', 'footway'], ['highway', 'service'], ['amenity', 'parking']],
    'rare_tags': [['amenity', 'drinking_water'], ['shop', 'florist'], ['man_made', 'tower']],
    'keys': ['amenity', 'highway', 'building', 'shop', 'name', 'natural', 'landuse'],
    'node_ids': [1001 + i for i in range(50)],
    'node_coords': [[44.60 + 0.002 * i, -63.60 + 0.003 * i] for i in range(50)],
    'way_ids': [2001 + i for i in range(50)],
    'relation_ids': [3001 + i for i in range(50)],
    'roles': ['outer', 'inner', 'stop', 'platform'],
    'bbox': [44.5, -63.7, 44.8, -63.4],
    'area_names': ['Halifax', 'Dartmouth', 'Bedford'],
    'area_relation_ids': [9092218, 9344588],
    'users': ['user_a', 'user_b'],
    'uids': [11, 22],
    'edit_times': ['2015-01-01T00:00:00Z', '2018-06-01T12:00:00Z', '2022-03-04T05:06:07Z'],
    'time_range': ['2015-01-01T00:00:00Z', '2022-03-04T05:06:07Z'],
}

# Constructs each feature set must produce, as (description, regex, minimum share of queries).
# The shares are well below what the generator produces, so they flag a construct that has
# stopped being generated, not small changes in weighting.
BASE = [
    ('id filter', r'\((id:)?\d+(,\d+)*\)', 0.05),
    ('bbox filter', r'\(-?\d+\.\d+,-?\d+\.\d+,-?\d+\.\d+,-?\d+\.\d+\)', 0.2),
    ('global [bbox:]', r'\[bbox:', 0.01),
    ('around a point', r'around:\d+,', 0.02),
    ('around a set', r'around\.\w+:', 0.02),
    ('poly: filter', r'\(poly:', 0.05),
    ('poly: with single quotes', r"\(poly:'", 0.01),
    ('negated regex', r'!~', 0.005),
    ('negated alternation (osmnx)', r'!~"[^"]*\|[^"]*"', 0.005),
    ('anchored alternation (osmdata)', r'~"\^\([^"]*\|[^"]*\)\$"', 0.003),
    ('recursion down', r'>', 0.05),
    ('recursion up', r'<', 0.02),
    ('member filter', r'\((w|r|bn|bw|br)(\.\w+)?(:"[^"]*")?\)', 0.05),
    ('foreach', r'foreach', 0.01),
    ('if: evaluator', r'\(if:', 0.02),
    ('out meta', r'out [^;]*meta', 0.05),
    ('out geom', r'out [^;]*geom', 0.02),
    ('out center', r'out [^;]*center', 0.01),
    ('qt sorting', r'out [^;]*qt', 0.01),
    ('JSON output', r'\[out:json\]', 0.05),
    ('CSV output', r'\[out:csv', 0.05),
    ('out count', r'out count', 0.05),
]
FEATURES = {
    'areas': [
        ('area by name', r'area\[name=', 0.02),
        ('area by id, area(id:N)', r'area\(id:36\d+\)', 0.005),
        ('area by id, area(N)', r'area\(36\d+\)', 0.005),
        ('area filter', r'\(area\.\w+\)', 0.02),
        ('is_in', r'is_in', 0.01),
        ('map_to_area', r'map_to_area', 0.005),
        ('pivot', r'pivot\.', 0.005),
    ],
    'history': [
        ('[date:]', r'\[date:', 0.02),
        ('[diff:] or [adiff:]', r'\[a?diff:', 0.02),
        ('newer', r'newer:', 0.01),
        ('changed', r'changed:', 0.01),
    ],
    'users': [
        ('uid or user filter', r'\((uid|user):', 0.01),
        ('touched filter', r'_touched:', 0.005),
    ],
    'complete': [
        ('complete loop', r'complete\(', 0.02),
    ],
    'evaluators': [
        ('convert', r' convert (item|row|stat) ', 0.05),
        ('make', r'(^|\n)make ', 0.05),
        ('for loop', r'for\.s\d+->', 0.02),
        ('for over keys()', r'for\.s\d+->\.s\d+ \(keys\(\)\)', 0.002),
        ('if/else', r'if \(.*\} else \{', 0.01),
        ('per_member', r'per_member\(', 0.005),
        ('aggregate', r'\.(u|set|sum|min|max|count)\(', 0.05),
        ('geometry constructor', r'(pt|lstr|poly)\(', 0.02),
        ('hull or trace', r'(hull|trace)\(', 0.02),
        ('list function', r'lrs_(in|union|isect|min|max)\(', 0.05),
        ('ternary', r'\?\(', 0.05),
    ],
    'poi': [
        ('union of 4+ statements around one point', r'\((?:\s*\w+\[[^\n]*\(around:\d+,-?[\d.]+,-?[\d.]+\);){4,}', 0.2),
        ('around radius >= 2000 m', r'around:(?:[2-9]\d{3}|\d{5,}),', 0.2),
        ('out center and tags', r'out (?:center tags|tags center)', 0.08),
        ('case-insensitive alternation', r'~"[^"]*\|[^"]*",i\]', 0.05),
    ],
}
FEATURES['regex'] = [
    ('wildcard or character class', r'~"[^"]*(\.\*|\.\+|\.\?|\[[^"\]]*\])', 0.02),
    ('suffix anchor only', r'~"[^"^]*\$"', 0.02),
    ('partly anchored list', r'~"\^\([^"]*\)"|~"\([^"]*\)\$"|~"\^[^"(]*\|[^"]*"', 0.02),
    ('special pattern "." or ".*"', r'~"\.\*?"', 0.03),
    ('multivalue idiom', r'\(\^\|;\)', 0.02),
    ('case-insensitive in changed case', r'~"[^"]*[A-Z][^"]*",i\]', 0.02),
    ('key alternation', r'\[~"\^\([^"]*\|', 0.01),
    # The synthetic vocabulary has 12 tag values, so lists stop there; real ones reach 25-70.
    ('long list', r'~"(?:[^"|]*\|){11,}', 0.01),
    ('empty value', r'~"\^\$"', 0.002),
]
FEATURES['output'] = [
    ('csv header', r'\[out:csv\(', 0.2),
    ('csv count field', r'::"count:(nodes|ways|relations|areas)"', 0.02),
    ('csv header line or separator', r'\[out:csv\([^)]*;(true|false)', 0.05),
    ('popup', r'\[out:popup\(', 0.05),
    ('json', r'\[out:json\]', 0.1),
    ('timeout setting', r'\[timeout:', 0.3),
    ('maxsize setting', r'\[maxsize:', 0.3),
    ('out noids', r'out [^;]*noids', 0.05),
    ('out count', r'out [^;]*count', 0.05),
    ('out quirks (error path)', r'out [^;]*quirks', 0.002),
    ('out limit', r'out [^;]*\b(1|2|5|17|100)\b', 0.05),
]
FEATURES['statements'] = [
    ('local', r' local( ll| llb)?->', 0.02),
    ('local llb', r' local llb->', 0.005),
    ('regex key', r'\[~"', 0.02),
    ('case-insensitive regex', r',i\]', 0.02),
    ('polyline around', r'\(around:\d+,[-0-9.]+,[-0-9.]+,[-0-9.]+,[-0-9.]+', 0.02),
    ('member filter with role', r'\((r|bn|bw|br)\.s\d+:"', 0.02),
    ('foreach variant', r'foreach(\.s\d+)?(->\.x)?\(', 0.02),
]
# Rules for combinations of features.
COMBINED = {
    frozenset({'history', 'statements'}): [('compare', r' compare(\(\)|\(delta:)?->', 0.01)],
    frozenset({'areas', 'statements'}): [('area filter by id', r'\(area:36\d+\)', 0.005)],
}
# Share of queries that may fail to parse. evaluators breaks some expressions on purpose.
PARSE_ALLOWANCE = {'evaluators': 0.02}

COUNT = 2000


def check_coverage(features, queries, failures):
    # With poi, half of the queries are POI lookups, so the other constructs are half as common.
    base_scale = 0.5 if 'poi' in features else 1.0
    rules = [(n, p, share * base_scale) for n, p, share in BASE] + [rule for f in sorted(features) for rule in FEATURES[f]]
    rules += [rule for need, extra in COMBINED.items() if need <= features for rule in extra]
    for name, pattern, share in rules:
        hits = sum(1 for q in queries if re.search(pattern, q))
        if hits < share * len(queries):
            failures.append(f'{"+".join(sorted(features)) or "base"}: {name}: {hits} of '
                            f'{len(queries)}, expected at least {share:.1%}')


def check_parse(bindir, features, queries, failures):
    errors = 0
    examples = []
    for q in queries:
        p = subprocess.run([os.path.join(bindir, 'osm3s_query'), '--dump-xml'], input=q.encode(),
                           capture_output=True, timeout=60)
        out = (p.stdout + p.stderr).decode(errors='replace')
        if re.search(r'(static|parse) error', out):
            errors += 1
            if len(examples) < 3:
                examples.append(next(l for l in out.split('\n') if 'error' in l))
    label = '+'.join(sorted(features)) or 'base'
    print(f'  {label}: {errors} of {len(queries)} queries fail to parse')
    allowance = max([0.01] + [PARSE_ALLOWANCE.get(f, 0.01) for f in features])
    if errors > allowance * len(queries):
        failures.append(f'{label}: {errors} of {len(queries)} queries fail to parse, e.g. '
                        + ' | '.join(examples))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--bin', help='bin directory of an Overpass build, to parse the queries')
    args = parser.parse_args()

    failures = []
    for features in [frozenset(), frozenset({'areas', 'complete'}),
                     frozenset({'history', 'users', 'complete'}), frozenset({'evaluators'}),
                     frozenset({'output'}), frozenset({'statements', 'areas'}),
                     frozenset({'history', 'users', 'statements'}),
                     frozenset({'evaluators', 'output', 'statements', 'poi', 'areas', 'complete'}),
                     frozenset({'poi'}), frozenset({'regex'}), frozenset({'regex', 'poi'})]:
        queries = [fuzz.generate(VOCAB, 7, i, features) for i in range(COUNT)]
        # The rules' shares assume one new family at a time; the combined set only gets parsed.
        if len(features & {'evaluators', 'output', 'statements'}) <= 1:
            check_coverage(features, queries, failures)
        # Same seed and index, same query.
        if queries[:50] != [fuzz.generate(VOCAB, 7, i, features) for i in range(50)]:
            failures.append(f'{sorted(features)}: generation is not reproducible')
        if args.bin:
            check_parse(args.bin, features, queries, failures)

    # The baseline cache, with a fake osm3s_query (bench/test_fuzz_cache.py).
    cache = subprocess.run([sys.executable, os.path.join(HERE, 'test_fuzz_cache.py')],
                           capture_output=True, text=True, timeout=600)
    print('  baseline cache: ' + (cache.stdout.strip().split('\n')[-1] if cache.stdout else 'no output'))
    if cache.returncode != 0:
        failures.append('baseline cache: ' + '; '.join(
            l for l in cache.stdout.split('\n') if l.startswith('FAIL')) or cache.stderr[-500:])

    for f in failures:
        print('FAIL', f)
    print('ok' if not failures else f'{len(failures)} failures')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
