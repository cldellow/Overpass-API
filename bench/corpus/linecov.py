#!/usr/bin/env python3
"""Line coverage of each corpus query, from a source-based coverage build (clang
-fprofile-instr-generate -fcoverage-mapping), for the coverage site (site.py).

For each query of a corpus (select.py's or coverage.py's JSON lines) it runs the query, and with
--dumps the parser's three dump modes, with its own LLVM_PROFILE_FILE, then exports the lines
it executed with llvm-cov. Writes:

  OUT/lines.jsonl   one line per query: {"i": index, "id": id, "status", "digest",
                    "lines": {file: [line, ...]}}, files relative to --src
  OUT/total.json    for every instrumented file: {file: {line: count}} summed over the corpus
                    (a line with count 0 was instrumented and never run)

The per-query digests must match the ones in the corpus: a mismatch means the build or the
database differs from the one the corpus was selected on, and is reported."""
import argparse
import json
import multiprocessing
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import fuzz  # noqa: E402

DUMP_MODES = ('--dump-xml', '--dump-pretty-ql', '--dump-compact-ql')


def lcov_lines(text, src):
    """Parse lcov: {file relative to src: {line: count}} for files under src."""
    out, cur = {}, None
    for line in text.splitlines():
        if line.startswith('SF:'):
            path = os.path.realpath(line[3:])
            cur = out.setdefault(os.path.relpath(path, src), {}) if path.startswith(src + os.sep) else None
        elif line.startswith('DA:') and cur is not None:
            ln, count = line[3:].split(',')[:2]
            cur[int(ln)] = cur.get(int(ln), 0) + int(count)
    return out


def export(binary, profdata, src):
    text = subprocess.run(['llvm-cov', 'export', '-format=lcov', '-skip-functions', '-instr-profile', profdata,
                           binary], capture_output=True, text=True, check=True).stdout
    return lcov_lines(text, src)


def work(task):
    i, rec, bindir, db, timeout, tmp, src, dumps = task
    d = os.path.join(tmp, str(i))
    os.makedirs(d, exist_ok=True)
    os.environ['LLVM_PROFILE_FILE'] = os.path.join(d, '%p.profraw')
    status, digest, _out, _peak = fuzz.run_once(bindir, db, rec['query'], timeout)
    if dumps:
        for mode in DUMP_MODES:
            try:
                subprocess.run([os.path.join(bindir, 'osm3s_query'), mode], input=rec['query'].encode(),
                               capture_output=True, timeout=timeout)
            except subprocess.TimeoutExpired:
                pass
    raws = [os.path.join(d, f) for f in os.listdir(d) if f.endswith('.profraw')]
    profdata = os.path.join(tmp, f'{i}.profdata')
    subprocess.run(['llvm-profdata', 'merge', '-sparse', '-o', profdata] + raws, check=True)
    shutil.rmtree(d)
    lines = export(os.path.join(bindir, 'osm3s_query'), profdata, src)
    hit = {f: sorted(ln for ln, c in ls.items() if c > 0) for f, ls in lines.items()}
    return {'i': i, 'id': rec['id'], 'status': status, 'digest': digest,
            'expected': (rec['status'], rec['digest']), 'lines': {f: v for f, v in hit.items() if v}}, profdata


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('corpus', help='JSON lines with id, query, status and digest')
    ap.add_argument('--bin', required=True, help='bin directory of the source-based coverage build')
    ap.add_argument('--src', required=True, help='the build\'s source directory')
    ap.add_argument('--db', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--timeout', type=int, default=120)
    ap.add_argument('--jobs', type=int, default=os.cpu_count())
    ap.add_argument('--dumps', action='store_true')
    args = ap.parse_args()

    src = os.path.realpath(args.src)
    recs = [json.loads(line) for line in open(args.corpus)]
    os.makedirs(args.out, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix='linecov-')
    tasks = [(i, r, args.bin, args.db, args.timeout, tmp, src, args.dumps) for i, r in enumerate(recs)]
    profdatas, mismatches = [], []
    with open(os.path.join(args.out, 'lines.jsonl'), 'w') as out, multiprocessing.Pool(args.jobs) as pool:
        for n, (rec, profdata) in enumerate(pool.imap_unordered(work, tasks), 1):
            if (rec['status'], rec['digest']) != tuple(rec.pop('expected')):
                mismatches.append(rec['id'])
            out.write(json.dumps(rec, separators=(',', ':')) + '\n')
            profdatas.append(profdata)
            if n % 100 == 0:
                print(f'{n}/{len(tasks)}', file=sys.stderr, flush=True)

    # The corpus total, with the instrumented lines that no query ran.
    total = os.path.join(tmp, 'total.profdata')
    listing = os.path.join(tmp, 'inputs.txt')
    with open(listing, 'w') as f:
        f.write('\n'.join(profdatas) + '\n')
    subprocess.run(['llvm-profdata', 'merge', '-sparse', '-o', total, '-f', listing], check=True)
    lines = export(os.path.join(args.bin, 'osm3s_query'), total, src)
    with open(os.path.join(args.out, 'total.json'), 'w') as f:
        json.dump({fn: {str(k): v for k, v in sorted(ls.items())} for fn, ls in sorted(lines.items())}, f,
                  separators=(',', ':'))
    shutil.rmtree(tmp)
    print(f'{len(recs)} queries; {len(mismatches)} with a status or digest unlike the corpus\'s'
          + (': ' + ', '.join(mismatches[:20]) if mismatches else ''), file=sys.stderr)


if __name__ == '__main__':
    main()
