#!/usr/bin/env python3
"""Write values for the {{NAME}} placeholders in benchmark queries, chosen from a database.

  pick_vars.py --bin BUILD/bin --db DB_DIR --out VARS.json

Values:
  UID   the user id with the most current objects in the Halifax bbox
        (44.60,-63.66,44.70,-63.52), so that user filters have real work to do

The values come from the database, so for a licensed extract the output file is covered
by the same terms as the extract: keep it with the database and never commit or publish
it. This script prints only counts, not the values.
"""
import argparse
import collections
import json
import os
import subprocess
import sys

BBOX = '44.60,-63.66,44.70,-63.52'


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--bin', required=True, help='bin directory of an Overpass build')
    parser.add_argument('--db', required=True, help='database directory (imported with --meta or --keep-attic)')
    parser.add_argument('--out', required=True, help='JSON file to write')
    args = parser.parse_args()

    query = f'[out:csv(::uid;false)][timeout:600]; nwr({BBOX}); out meta;'
    proc = subprocess.run([os.path.join(args.bin, 'osm3s_query'), '--db-dir=' + args.db.rstrip('/') + '/'],
                          input=query.encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    counts = collections.Counter(line for line in proc.stdout.decode().split('\n') if line.strip().isdigit())
    if not counts:
        sys.exit('no user ids found: was the database imported with --meta or --keep-attic?')
    uid, n = counts.most_common(1)[0]
    with open(os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as f:
        json.dump({'UID': int(uid)}, f)
    print(f'{args.out}: UID has {n} of {sum(counts.values())} current objects in the bbox '
          f'({len(counts)} distinct users)')


if __name__ == '__main__':
    main()
