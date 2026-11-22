#!/usr/bin/env python3
"""Mutation check for props.py: does it notice when a change is broken on purpose?

Each mutant in a JSON file is one exact string replacement in a source file, written against a
particular tree (e.g. all-changes). For each mutant this script applies it to SRC's working
tree, rebuilds osm3s_query in BUILD, runs props.py against the baseline, and restores the file.
A mutant props.py doesn't notice marks a change whose correctness the tests don't cover (or an
equivalent mutant: one that can't change any result).

    python3 bench/props/mutate.py --src ALL_CHANGES_CHECKOUT --build ALL_CHANGES_CHECKOUT/build \\
        --baseline UPSTREAM/build/bin --mutants bench/props/mutants-all-changes.json --seeds 1-60

Use a checkout and build directory dedicated to this: the files are modified while it runs
(each is restored afterwards, also on Ctrl-C), and the build is left rebuilt from the
restored sources at the end.

Mutant fields: commit, subject (optional), file (relative to SRC), old (must occur exactly
once), new, and optionally note.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def build(build_dir, jobs):
    p = subprocess.run(['make', '-j', str(jobs), 'bin/osm3s_query'], cwd=build_dir,
                       capture_output=True, text=True)
    return p.returncode == 0, p.stdout[-3000:] + p.stderr[-3000:]


def run_props(args):
    cmd = [sys.executable, args.props, '--bin', os.path.join(args.build, 'bin'),
           '--baseline', args.baseline, '--seeds', args.seeds, '--jobs', str(args.jobs)]
    if args.import_bin:
        cmd += ['--import-bin', args.import_bin]
    if args.history_import_bin:
        cmd += ['--history-import-bin', args.history_import_bin]
    p = subprocess.run(cmd, capture_output=True, text=True)
    out = p.stdout
    # The summary table: name (may contain spaces), runs, fail, baseline too, differ, fixed.
    stats = {}
    for line in out.splitlines():
        parts = line.rsplit(None, 5)
        if len(parts) != 6 or not all(re.fullmatch(r'\d+|-', x) for x in parts[1:]):
            continue
        stats[parts[0]] = [int(x) if x.isdigit() else 0 for x in parts[2:]]
    return p.returncode, stats, out


def changes(clean, stats):
    """Rows where a mutant fails more, differs more or fixes less than the unmutated tree.
    Comparing with the unmutated run (not only with the baseline) also catches a mutant that
    undoes a bug fix, whose output then matches the baseline again."""
    found = []
    for name, (fail, _, differ, fixed) in sorted(stats.items()):
        f0, _, d0, x0 = clean.get(name, [0, 0, 0, 0])
        parts = []
        if differ > d0:
            parts.append(f'{differ - d0} differ')
        if fail > f0:
            parts.append(f'{fail - f0} more fail')
        if fixed < x0:
            parts.append(f'{x0 - fixed} fewer fixed')
        if parts:
            found.append(f'{name} ({", ".join(parts)})')
    return found


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--src', required=True, help='source checkout the mutants apply to')
    ap.add_argument('--build', required=True, help='its build directory')
    ap.add_argument('--baseline', required=True, help='baseline bin directory (e.g. upstream)')
    ap.add_argument('--import-bin', help='passed to props.py')
    ap.add_argument('--history-import-bin', help='passed to props.py (adds the history checks)')
    ap.add_argument('--mutants', required=True)
    ap.add_argument('--only', help='comma-separated commits to run')
    ap.add_argument('--seeds', default='1-60')
    ap.add_argument('--jobs', type=int, default=os.cpu_count() or 1)
    ap.add_argument('--props', default=os.path.join(HERE, 'props.py'), help='props.py to run (default: the one beside this script)')
    ap.add_argument('--log-dir', help='write each mutant\'s props.py output and build errors here')
    args = ap.parse_args()

    mutants = json.load(open(args.mutants))
    if args.only:
        wanted = set(args.only.split(','))
        mutants = [m for m in mutants if m['commit'] in wanted]
    if args.log_dir:
        os.makedirs(args.log_dir, exist_ok=True)

    ok, log = build(args.build, args.jobs)
    if not ok:
        raise SystemExit('the unmutated tree does not build:\n' + log)
    code, clean, out = run_props(args)
    if code != 0:
        raise SystemExit('props.py fails on the unmutated tree; fix that first:\n' + out[-3000:])

    results = []
    for m in mutants:
        path = os.path.join(args.src, m['file'])
        label = f'{m["commit"]} {m.get("subject", "")}'.strip()
        start = time.time()
        if not os.path.exists(path):
            results.append((m, 'not applicable', 'file missing'))
            continue
        original = open(path, encoding='utf-8', errors='surrogateescape').read()
        count = original.count(m['old'])
        if count != 1:
            results.append((m, 'not applicable', f'old text found {count} times'))
            print(f'{label}: not applicable (old text found {count} times)', flush=True)
            continue
        try:
            with open(path, 'w', encoding='utf-8', errors='surrogateescape') as f:
                f.write(original.replace(m['old'], m['new'], 1))
            ok, log = build(args.build, args.jobs)
            if not ok:
                status, detail = 'build failed', log.strip().splitlines()[-1] if log.strip() else ''
                if args.log_dir:
                    open(os.path.join(args.log_dir, f'{m["commit"]}.build.txt'), 'w').write(log)
            else:
                code, stats, out = run_props(args)
                if args.log_dir:
                    open(os.path.join(args.log_dir, f'{m["commit"]}.props.txt'), 'w').write(out)
                found = changes(clean, stats)
                if code > 1 or not stats:
                    status, detail = 'error', out.strip().splitlines()[-1] if out.strip() else ''
                elif found:
                    status, detail = 'caught', ', '.join(found)
                else:
                    status, detail = 'not caught', ''
        finally:
            with open(path, 'w', encoding='utf-8', errors='surrogateescape') as f:
                f.write(original)
        results.append((m, status, detail))
        print(f'{label}: {status} {detail} ({time.time() - start:.0f}s)', flush=True)

    ok, log = build(args.build, args.jobs)
    if not ok:
        print('warning: rebuilding the restored tree failed:\n' + log, file=sys.stderr)

    print()
    print('| Commit | Change | Result | Checks that caught it |')
    print('|---|---|---|---|')
    for m, status, detail in results:
        print(f'| {m["commit"]} | {m.get("subject", "")} | {status} | {detail} |')
    caught = sum(1 for _, s, _ in results if s == 'caught')
    print(f'\n{caught} of {len(results)} mutants caught')
    return 0


if __name__ == '__main__':
    sys.exit(main())
