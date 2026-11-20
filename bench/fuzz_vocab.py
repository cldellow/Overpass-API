#!/usr/bin/env python3
"""Build a vocabulary for fuzz.py from an OSM extract, so generated queries hit real data.

Collects common and rare tags, a random sample of node/way/relation ids with a
coordinate for each node, relation roles, and the data's bounding box.
Needs osmium-tool.

Usage:
  fuzz_vocab.py EXTRACT.osm.pbf VOCAB.json [--seed 1]
"""
import argparse
import collections
import json
import random
import subprocess


def tag_counts(pbf):
    """Return [(key, value, count)] for every tag, most common first."""
    out = subprocess.run(['osmium', 'tags-count', '--sort=count-desc', '--min-count=2', pbf, '*=*'],
                         check=True, stdout=subprocess.PIPE, text=True).stdout
    tags = []
    for line in out.splitlines():
        # Format: count<TAB>"key"<TAB>"value"
        parts = line.split('\t')
        if len(parts) != 3:
            continue
        count, key, value = int(parts[0]), parts[1][1:-1], parts[2][1:-1]
        tags.append((key, value, count))
    return tags


def sample_objects(pbf, per_type, rng):
    """Reservoir-sample objects from the OPL dump; return ids, node coords and roles."""
    samples = {'n': [], 'w': [], 'r': []}
    seen = collections.Counter()
    roles = collections.Counter()
    proc = subprocess.Popen(['osmium', 'cat', '-f', 'opl', pbf], stdout=subprocess.PIPE, text=True)
    for line in proc.stdout:
        kind = line[0]
        if kind not in samples:
            continue
        fields = line.split(' ')
        oid = int(fields[0][1:])
        item = {'id': oid}
        if kind == 'n':
            for f in fields:
                if f.startswith('x'):
                    item['lon'] = float(f[1:])
                elif f.startswith('y'):
                    item['lat'] = float(f[1:].strip())
            if 'lat' not in item or 'lon' not in item:
                continue
        elif kind == 'r':
            for f in fields:
                if f.startswith('M') and len(f) > 1:
                    for member in f[1:].strip().split(','):
                        if '@' in member:
                            roles[member.split('@', 1)[1]] += 1
        seen[kind] += 1
        bucket = samples[kind]
        if len(bucket) < per_type:
            bucket.append(item)
        else:
            j = rng.randrange(seen[kind])
            if j < per_type:
                bucket[j] = item
    proc.wait()
    return samples, roles


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('pbf')
    parser.add_argument('vocab')
    parser.add_argument('--seed', type=int, default=1)
    parser.add_argument('--per-type', type=int, default=3000, help='ids to sample per object type')
    args = parser.parse_args()
    rng = random.Random(args.seed)

    tags = tag_counts(args.pbf)
    # Skip unique-ish keys whose values are noise for querying (names, refs, notes...).
    noisy = ('name', 'addr:', 'note', 'description', 'source', 'ref', 'fixme', 'website', 'phone',
             'wikidata', 'wikipedia', 'opening_hours', 'email', 'url', 'image', 'old_name', 'alt_name')
    usable = [t for t in tags if not t[0].startswith(noisy) and '"' not in t[1] and '\\' not in t[1]]
    common = usable[:300]
    rare = [t for t in usable if 2 <= t[2] <= 50]
    rng.shuffle(rare)
    keys = collections.Counter()
    for key, _, count in tags:
        keys[key] += count

    samples, roles = sample_objects(args.pbf, args.per_type, rng)
    nodes = samples['n']
    lats = [n['lat'] for n in nodes]
    lons = [n['lon'] for n in nodes]
    vocab = {
        'common_tags': [[k, v] for k, v, _ in common],
        'rare_tags': [[k, v] for k, v, _ in rare[:300]],
        'keys': [k for k, _ in keys.most_common(150)],
        'node_ids': [n['id'] for n in nodes],
        'node_coords': [[n['lat'], n['lon']] for n in nodes],
        'way_ids': [w['id'] for w in samples['w']],
        'relation_ids': [r['id'] for r in samples['r']],
        'roles': [r for r, _ in roles.most_common(30) if '"' not in r and "'" not in r],
        'bbox': [min(lats), min(lons), max(lats), max(lons)],
    }
    with open(args.vocab, 'w') as f:
        json.dump(vocab, f)
    print(f'{len(common)} common tags, {len(rare)} rare tags, {len(nodes)} nodes, '
          f'{len(samples["w"])} ways, {len(samples["r"])} relations, {len(vocab["roles"])} roles')


if __name__ == '__main__':
    main()
