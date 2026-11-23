#!/usr/bin/env python3
"""Check that no query of a corpus contains a user name or uid from a fuzz.py vocabulary (one built
from a restricted extract), before the corpus is published.

  leakcheck.py CORPUS.jsonl --vocab VOCAB.json

Fails on any quoted string equal to a user name in the vocabulary (unless it is also a tag key,
tag value or area name there, so it came from the tags), on any user or uid filter, and on any
uid() comparison with a number that is a uid there. Also reports, without failing, numbers equal to a uid: element ids and
limits often coincide with one. Prints counts and query ids only, never the values."""
import argparse
import json
import re
import sys

QUOTED = re.compile(r'"((?:[^"\\]|\\.)*)"|\'((?:[^\'\\]|\\.)*)\'')
USER_FILTER = re.compile(r'\((?:user|uid|user_touched|uid_touched)\s*:')
UID_COMPARISON = re.compile(r'uid\(\)\s*(?:==|!=|<=|>=|<|>)\s*(\d+)')
NUMBER = re.compile(r'(?<![\w.])\d+(?![\w.])')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('corpus')
    ap.add_argument('--vocab', required=True)
    args = ap.parse_args()
    v = json.load(open(args.vocab))
    users = {str(u) for u in v.get('users', [])}
    uids = {str(u) for u in v.get('uids', [])}
    from_tags = set(v.get('keys', [])) | set(v.get('area_names', []))
    for name in ('common_tags', 'rare_tags'):
        for kv in v.get(name, []):
            from_tags.update(str(x) for x in kv)
    users -= from_tags

    hits = {'quoted user name': [], 'user or uid filter': [], 'uid() compared with a uid': [],
            'number equal to a uid (not failing)': []}
    n = 0
    for line in open(args.corpus):
        r = json.loads(line)
        q = r['query']
        n += 1
        if any((a or b) in users for a, b in QUOTED.findall(q)):
            hits['quoted user name'].append(r['id'])
        if USER_FILTER.search(q):
            hits['user or uid filter'].append(r['id'])
        if any(x in uids for x in UID_COMPARISON.findall(q)):
            hits['uid() compared with a uid'].append(r['id'])
        if any(x in uids for x in NUMBER.findall(q)):
            hits['number equal to a uid (not failing)'].append(r['id'])
    print(f'{n} queries checked against {len(users)} user names and {len(uids)} uids')
    for k, ids in hits.items():
        print(f'  {k}: {len(ids)}' + (f' (e.g. {", ".join(ids[:5])})' if ids else ''))
    failing = [k for k in hits if 'not failing' not in k]
    sys.exit(1 if any(hits[k] for k in failing) else 0)


if __name__ == '__main__':
    main()
