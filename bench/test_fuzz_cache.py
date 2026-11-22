#!/usr/bin/env python3
"""Tests for fuzz.py's baseline cache, using a fake osm3s_query, so no Overpass build or database
is needed.

  python3 bench/test_fuzz_cache.py

The fake prints a digest of its query, and logs each call, so the tests can count how often the
baseline really ran. Environment variables change its behaviour without changing the binary:
FAKE_A_MODE / FAKE_B_MODE set to "changed" alter the output, and "timeout" makes it sleep past
--timeout. Exit status is 1 if any check fails.
"""
import csv
import json
import os
import re
import stat
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from test_fuzz import VOCAB  # noqa: E402

FAKE = r'''#!/usr/bin/env python3
# ROLE
import hashlib, os, sys, time
role = os.path.basename(os.path.dirname(os.path.abspath(__file__)))
query = sys.stdin.buffer.read()
with open(os.environ['FAKE_LOG'], 'a') as f:
    f.write(role + '\n')
mode = os.environ.get('FAKE_' + role.upper() + '_MODE', '')
if mode == 'timeout':
    time.sleep(5)
digest = hashlib.sha256(query).hexdigest()
if mode == 'changed':
    digest = 'changed-' + digest
sys.stdout.write('<osm>\n' + '\n'.join(digest[i:i + 8] for i in range(0, 64, 8)) + '\n</osm>\n')
'''

COUNT = 6


class Env:
    def __init__(self, root):
        self.root = root
        self.vocab = os.path.join(root, 'vocab.json')
        with open(self.vocab, 'w') as f:
            json.dump(VOCAB, f)
        self.db = os.path.join(root, 'db')
        os.makedirs(self.db)
        for name in ('nodes.bin', 'nodes.bin.idx', 'nodes.map'):
            with open(os.path.join(self.db, name), 'w') as f:
                f.write('data')
        self.bins = {}
        for role in ('a', 'b'):
            d = os.path.join(root, role)
            os.makedirs(d)
            path = os.path.join(d, 'osm3s_query')
            with open(path, 'w') as f:
                f.write(FAKE.replace('ROLE', role))
            os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
            self.bins[role] = d
        self.cache = os.path.join(root, 'cache')
        self.log = os.path.join(root, 'calls.log')

    def run(self, *extra, a_mode='', b_mode='', report=None):
        open(self.log, 'w').close()
        env = dict(os.environ, FAKE_LOG=self.log, FAKE_A_MODE=a_mode, FAKE_B_MODE=b_mode)
        cmd = [sys.executable, os.path.join(HERE, 'fuzz.py'), '--a', self.bins['a'], '--b', self.bins['b'],
               '--db', self.db, '--vocab', self.vocab, '--seed', '5', '--count', str(COUNT), '--jobs', '3',
               '--timeout', '2', '--cache-dir', self.cache, '--out-dir', os.path.join(self.root, 'failures')]
        if report:
            cmd += ['--report', report]
        p = subprocess.run(cmd + list(extra), env=env, capture_output=True, text=True, timeout=120)
        with open(self.log) as f:
            calls = f.read().split()
        return p, calls.count('a'), calls.count('b')


def verdicts(stdout):
    m = re.search(r'verdicts: (\{.*\})', stdout)
    return eval(m.group(1)) if m else None  # the dict fuzz.py printed


def main():
    failures = []

    def expect(name, condition, detail=''):
        print(('ok   ' if condition else 'FAIL ') + name + (f' ({detail})' if detail and not condition else ''))
        if not condition:
            failures.append(name)

    with tempfile.TemporaryDirectory() as root:
        e = Env(root)

        p, a, b = e.run()
        expect('first run runs the baseline for every query', a == COUNT and b == COUNT, f'a={a} b={b}')
        expect('first run: all same', verdicts(p.stdout) == {'same': COUNT}, p.stdout)

        p, a, b = e.run()
        expect('second run reuses every baseline outcome', a == 0 and b == COUNT, f'a={a} b={b}')
        expect('second run: all same', verdicts(p.stdout) == {'same': COUNT}, p.stdout)
        expect('second run reports the reuse', f'baseline: {COUNT} of {COUNT} outcomes reused' in p.stdout, p.stdout)

        report = os.path.join(root, 'report.csv')
        e.run(report=report)
        with open(report) as f:
            rows = list(csv.DictReader(f))
        expect('report marks cached baselines', len(rows) == COUNT and all(r['a_cached'] == '1' for r in rows))

        with open(os.path.join(e.db, 'transactions.log'), 'a') as f:
            f.write('a query ran\n')
        p, a, b = e.run()
        expect('a log file in the database directory keeps the cache', a == 0, f'a={a}')

        with open(os.path.join(e.db, 'nodes.bin'), 'a') as f:
            f.write('more data')
        p, a, b = e.run()
        expect('changing a data file invalidates the cache', a == COUNT, f'a={a}')

        with open(os.path.join(e.bins['a'], 'osm3s_query'), 'a') as f:
            f.write('# rebuilt\n')
        p, a, b = e.run()
        expect('changing the baseline binary invalidates the cache', a == COUNT, f'a={a}')

        p, a, b = e.run('--timeout', '3')
        expect('changing --timeout invalidates the cache', a == COUNT, f'a={a}')

        p, a, b = e.run(b_mode='changed')
        expect('a real difference reruns the baseline once per differing query', a == COUNT, f'a={a}')
        expect('a real difference is reported', verdicts(p.stdout) == {'DIFFERENT': COUNT}, p.stdout)
        expect('a real difference is not counted as stale', 'stale' not in p.stdout, p.stdout)
        a_out = os.path.join(root, 'failures', '5-0', 'a.out')
        expect('a.out holds the fresh baseline output', os.path.exists(a_out) and os.path.getsize(a_out) > 0)

        # The baseline's behaviour changes without its binary or database changing (as a
        # non-deterministic build would). --b now matches the new baseline, not the cached one.
        p, a, b = e.run(a_mode='changed', b_mode='changed')
        expect('a stale entry is detected and replaced', f'{COUNT} stale entries replaced' in p.stdout, p.stdout)
        expect('a stale entry does not produce a false difference', verdicts(p.stdout) == {'same': COUNT}, p.stdout)
        p, a, b = e.run(a_mode='changed', b_mode='changed')
        expect('replaced entries are reused', a == 0 and verdicts(p.stdout) == {'same': COUNT}, f'a={a}')

        p, a, b = e.run('--clear-cache')
        expect('--clear-cache empties the cache', a == COUNT, f'a={a}')

        p, a, b = e.run('--no-cache')
        expect('--no-cache runs the baseline every time', a == COUNT, f'a={a}')
        expect('--no-cache prints no reuse line', 'outcomes reused' not in p.stdout, p.stdout)

        p, a, b = e.run('--clear-cache', a_mode='timeout')
        expect('timeouts are skipped', verdicts(p.stdout) == {'skipped': COUNT}, p.stdout)
        p, a, b = e.run(a_mode='timeout')
        expect('timeouts are not cached', a == COUNT, f'a={a}')

        entries = [os.path.join(d, f) for d, _, fs in os.walk(e.cache) for f in fs]
        expect('no temporary files are left in the cache', not any(f.endswith('.tmp') for f in entries))

    print('ok' if not failures else f'{len(failures)} failures')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
