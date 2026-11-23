#!/usr/bin/env python3
"""Map each SanitizerCoverage edge of a coverage build to its source file, for select.py and
reduce.py --edgemap: writes {"files": [file of edge 0, file of edge 1, ...]}.

  edgefiles.py BINARY OUT.json

Runs the binary once with $COV_PCS set (cov_rt.c writes each edge's PC) and symbolizes the PCs
with llvm-symbolizer (without inlining, so an inlined header function maps to the header)."""
import json
import os
import subprocess
import sys
import tempfile


def main():
    binary, out = sys.argv[1], sys.argv[2]
    pcs = os.path.join(tempfile.mkdtemp(), 'pcs.txt')
    subprocess.run([binary, '--dump-xml'], input=b'node(1);out;', capture_output=True,
                   env=dict(os.environ, COV_PCS=pcs))
    addrs = ['0x' + x.strip() for x in open(pcs) if x.strip()]
    sym = subprocess.run(['llvm-symbolizer', f'--obj={binary}', '--no-inlines', '--functions=none',
                          '--output-style=GNU'], input='\n'.join(addrs) + '\n', capture_output=True,
                         text=True).stdout.split('\n')
    files = []
    for line in sym[:len(addrs)]:
        path = line.rsplit(':', 1)[0]
        files.append(os.path.normpath(path.split('/build/../src/', 1)[-1]) if '/src/' in path else path)
    json.dump({'files': files}, open(out, 'w'))
    print(f'{len(files)} edges mapped')


if __name__ == '__main__':
    main()
