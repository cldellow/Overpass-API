#!/usr/bin/env python3
"""Build a vocabulary for fuzz.py from an OSM extract, so generated queries hit real data.

Collects common and rare tags, a random sample of node/way/relation ids with a
coordinate for each node, relation roles, and the data's bounding box.
Needs osmium-tool.

Also collects names and ids of relations that become areas (boundaries, admin areas,
named multipolygons), and, given --osc-dir or --history, the users, user ids and edit timestamps
found in a directory of osmChange files (the regional diffs of a history database) or in a
full-history file (.osh.pbf).

The users and user ids come from the data, so for a licensed extract the vocabulary is
covered by the same terms: keep it with the data and never commit or publish it.

Usage:
  fuzz_vocab.py EXTRACT.osm.pbf VOCAB.json [--seed 1] [--osc-dir DIR | --history FILE.osh.pbf]
"""
import argparse
import collections
import glob
import json
import os
import random
import re
import subprocess
import xml.etree.ElementTree as ET


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


def area_names(pbf, limit=300):
    """Names and ids of named relations that the standard area rules turn into areas."""
    out = subprocess.run(
        'osmium tags-filter -O -f opl -o - "%s" r/boundary r/admin_level r/type=multipolygon' % pbf,
        shell=True, check=True, stdout=subprocess.PIPE, text=True).stdout
    names = collections.Counter()
    ids = []
    for line in out.splitlines():
        if not line.startswith('r'):
            continue
        for field in line.split(' '):
            if field.startswith('T'):
                for kv in field[1:].split(','):
                    if kv.startswith('name='):
                        name = kv[5:].replace('%20%', ' ').replace('%2c%', ',')
                        ids.append(int(line.split(' ')[0][1:]))
                        if '%' not in name and '"' not in name and '\\' not in name:
                            names[name] += 1
    ids = sorted(set(ids))
    ids = ids[::max(1, len(ids) // limit)][:limit]
    return [n for n, _ in names.most_common(limit)], ids


def edits_from_osc(osc_dir):
    """(user, uid, timestamp) of every object version in a directory of osmChange files."""
    for path in sorted(glob.glob(os.path.join(osc_dir, '*.osc'))):
        for _, el in ET.iterparse(path):
            if el.tag in ('node', 'way', 'relation'):
                yield el.get('user'), el.get('uid'), el.get('timestamp')
                el.clear()


def opl_unescape(text):
    return re.sub(r'%([0-9a-fA-F]+)%', lambda m: chr(int(m.group(1), 16)), text)


def edits_from_history(pbf):
    """(user, uid, timestamp) of every object version in a full-history file."""
    proc = subprocess.Popen(['osmium', 'cat', '-f', 'opl', '-o', '-', pbf],
                            stdout=subprocess.PIPE, text=True)
    for line in proc.stdout:
        user = uid = ts = None
        for field in line.split(' '):
            if field.startswith('u'):
                user = opl_unescape(field[1:]) or None
            elif field.startswith('i'):
                uid = field[1:] if field[1:] != '0' else None
            elif field.startswith('t'):
                ts = field[1:] or None
            elif field.startswith(('T', 'N', 'M', 'x', 'y')):
                break
        yield user, uid, ts
    proc.wait()


def history_vocab(edits):
    """Users, user ids and edit timestamps from (user, uid, timestamp) triples."""
    users, uids, times = collections.Counter(), collections.Counter(), []
    for user, uid, ts in edits:
        if user and '"' not in user and '\\' not in user:
            users[user] += 1
        if uid:
            uids[int(uid)] += 1
        if ts:
            times.append(ts)
    times.sort()
    return {
        'users': [u for u, _ in users.most_common(200)],
        'uids': [u for u, _ in uids.most_common(200)],
        'edit_times': times[::max(1, len(times) // 500)],
        'time_range': [times[0], times[-1]] if times else [],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('pbf')
    parser.add_argument('vocab')
    parser.add_argument('--seed', type=int, default=1)
    parser.add_argument('--per-type', type=int, default=3000, help='ids to sample per object type')
    parser.add_argument('--osc-dir', help='osmChange files of a history database, for users and dates')
    parser.add_argument('--history', help='full-history file (.osh.pbf), for users and dates')
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
    vocab['area_names'], vocab['area_relation_ids'] = area_names(args.pbf)
    if args.osc_dir:
        vocab.update(history_vocab(edits_from_osc(args.osc_dir)))
    elif args.history:
        vocab.update(history_vocab(edits_from_history(args.history)))
    with open(args.vocab, 'w') as f:
        json.dump(vocab, f)
    print(f'{len(common)} common tags, {len(rare)} rare tags, {len(nodes)} nodes, '
          f'{len(samples["w"])} ways, {len(samples["r"])} relations, {len(vocab["roles"])} roles, '
          f'{len(vocab["area_names"])} area names'
          + (f', {len(vocab["users"])} users, {len(vocab["edit_times"])} edit times {vocab["time_range"]}'
             if args.osc_dir or args.history else ''))


if __name__ == '__main__':
    main()
