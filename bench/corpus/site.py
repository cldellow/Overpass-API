#!/usr/bin/env python3
"""Build a static site showing what the conformance corpus exercises in the source: every source
file with its line coverage, and for each executed line the corpus queries that run it.

Inputs: the corpus (select.py's JSON lines), and linecov.py's output for it. Output: a directory
of plain HTML (no build step, no external assets) for GitHub Pages:

  index.html          totals, and line coverage by directory and file
  src/<file>.html     the source; click a line's query count for the queries that run it
  q/<n>.html          one corpus query: its text, expected result, and the lines only it runs
  corpus.jsonl        the corpus itself (id, query, status, digest)"""
import argparse
import collections
import html
import json
import os
import shutil

CSS = """
:root { --bg:#fff; --fg:#1b1f24; --muted:#59636e; --line:#d1d9e0; --hit:#dafbe1; --miss:#ffebe9;
  --hitfg:#116329; --missfg:#a40e26; --panel:#f6f8fa; --link:#0969da; --bar:#2da44e; --barbg:#ffd8d3; }
@media (prefers-color-scheme: dark) { :root { --bg:#0d1117; --fg:#e6edf3; --muted:#9198a1; --line:#3d444d;
  --hit:#12261e; --miss:#2d1517; --hitfg:#3fb950; --missfg:#f85149; --panel:#151b23; --link:#4493f8;
  --bar:#2ea043; --barbg:#4a1d1f; } }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--fg); font:14px/1.5 system-ui, sans-serif; }
main { max-width:1200px; margin:0 auto; padding:16px; }
a { color:var(--link); text-decoration:none; } a:hover { text-decoration:underline; }
h1 { font-size:22px; margin:8px 0; } h2 { font-size:17px; margin:24px 0 8px; }
.muted { color:var(--muted); }
table { border-collapse:collapse; width:100%; }
th, td { text-align:left; padding:4px 8px; border-bottom:1px solid var(--line); white-space:nowrap; }
td.num, th.num { text-align:right; font-variant-numeric:tabular-nums; }
tr.dir td { font-weight:600; background:var(--panel); }
.bar { display:inline-block; width:120px; height:10px; background:var(--barbg); vertical-align:middle; }
.bar span { display:block; height:100%; background:var(--bar); }
.scroll { overflow-x:auto; }
pre.src { margin:0; font:12.5px/1.45 ui-monospace, SFMono-Regular, Menlo, monospace; }
.src .l { display:grid; grid-template-columns:5em 6em 7em 1fr; }
.src .l > * { padding:0 6px; white-space:pre; }
.src .n { color:var(--muted); text-align:right; user-select:none; }
.src .c, .src .q { color:var(--muted); text-align:right; user-select:none; }
.src .hit { background:var(--hit); } .src .hit .c { color:var(--hitfg); }
.src .miss { background:var(--miss); } .src .miss .c { color:var(--missfg); }
.src .q a { cursor:pointer; }
.src .l:target { outline:2px solid var(--link); }
#panel { position:fixed; right:16px; bottom:16px; width:min(520px, calc(100vw - 32px)); max-height:60vh;
  overflow:auto; background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:12px;
  display:none; box-shadow:0 8px 24px rgba(0,0,0,.2); }
#panel.open { display:block; }
#panel pre { white-space:pre-wrap; font:12px/1.4 ui-monospace, monospace; margin:4px 0 12px; max-height:9em;
  overflow:auto; border-left:3px solid var(--line); padding-left:8px; }
pre.query { background:var(--panel); padding:12px; border-radius:6px; white-space:pre-wrap;
  font:13px/1.45 ui-monospace, monospace; }
.legend span { display:inline-block; padding:0 6px; margin-right:8px; }
"""

FILE_JS = """
const Q = JSON.parse(document.getElementById('attr').textContent);
const panel = document.getElementById('panel');
let corpus = null;
async function queries() {
  if (!corpus) corpus = (await (await fetch(ROOT + 'corpus.jsonl')).text()).trim().split('\\n').map(JSON.parse);
  return corpus;
}
document.addEventListener('click', async e => {
  const a = e.target.closest('a[data-l]');
  if (!a) { if (!e.target.closest('#panel')) panel.classList.remove('open'); return; }
  e.preventDefault();
  const [total, picks] = Q[a.dataset.l];
  let h = `<b>Line ${a.dataset.l}</b>: run by ${total} corpus quer${total == 1 ? 'y' : 'ies'}` +
    (picks.length < total ? `; the ${picks.length} cheapest:` : ':');
  for (const n of picks) h += `<div><a href="${ROOT}q/${n}.html">query ${n}</a><pre></pre></div>`;
  panel.innerHTML = h;
  panel.classList.add('open');
  const c = await queries();
  panel.querySelectorAll('pre').forEach((p, k) => p.textContent = c[picks[k]].query);
});
"""


def page(title, body, root, extra_head=''):
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{html.escape(title)}</title><link rel="stylesheet" href="{root}style.css">{extra_head}'
            f'</head><body><main>{body}</main></body></html>')


def pct(a, b):
    return f'{100 * a / b:.1f}%' if b else '–'


def bar(a, b):
    w = 100 * a / b if b else 0
    return f'<span class="bar"><span style="width:{w:.1f}%"></span></span>'


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--corpus', required=True)
    ap.add_argument('--linecov', required=True, help="linecov.py's output directory")
    ap.add_argument('--src', required=True)
    ap.add_argument('--commit', required=True, help='the source commit, for links and the title')
    ap.add_argument('--repo', default='https://github.com/fossgis/Overpass-API')
    ap.add_argument('--about', default='', help='an HTML paragraph for the index page')
    ap.add_argument('--per-line', type=int, default=15, help='queries listed per line')
    ap.add_argument('--out', required=True)
    args = ap.parse_args()

    corpus = [json.loads(line) for line in open(args.corpus)]
    total = json.load(open(os.path.join(args.linecov, 'total.json')))
    per_query = {}
    for line in open(os.path.join(args.linecov, 'lines.jsonl')):
        r = json.loads(line)
        per_query[r['i']] = r['lines']

    # line -> queries running it, cheapest first; and per query, the lines no other query runs.
    order = sorted(range(len(corpus)), key=lambda n: (corpus[n]['cpu'], len(corpus[n]['query'])))
    runs = collections.defaultdict(lambda: collections.defaultdict(list))
    for n in order:
        for f, lines in per_query.get(n, {}).items():
            for ln in lines:
                runs[f][ln].append(n)
    unique = collections.defaultdict(list)
    for f, by_line in runs.items():
        for ln, qs in by_line.items():
            if len(qs) == 1:
                unique[qs[0]].append((f, ln))

    if os.path.exists(args.out):
        shutil.rmtree(args.out)
    os.makedirs(args.out)
    with open(os.path.join(args.out, 'style.css'), 'w') as f:
        f.write(CSS)
    with open(os.path.join(args.out, '.nojekyll'), 'w') as f:
        pass
    with open(os.path.join(args.out, 'corpus.jsonl'), 'w') as f:
        for n, r in enumerate(corpus):
            f.write(json.dumps({'n': n, 'id': r['id'], 'query': r['query'], 'status': r['status'],
                                'digest': r['digest']}) + '\n')

    short = args.commit[:12]
    stats = {}
    for fn in sorted(total):
        counts = total[fn]
        path = os.path.join(args.src, fn)
        if not os.path.exists(path):
            continue
        src_lines = open(path, errors='replace').read().split('\n')
        hit = sum(1 for c in counts.values() if c > 0)
        stats[fn] = (hit, len(counts))
        depth = fn.count('/') + 1
        root = '../' * depth
        attr = {}
        rows = []
        for ln, text in enumerate(src_lines, 1):
            c = counts.get(str(ln))
            cls = '' if c is None else (' hit' if c > 0 else ' miss')
            qs = runs.get(fn, {}).get(ln, [])
            qcell = ''
            if qs:
                picks = qs[:args.per_line]
                attr[ln] = [len(qs), picks]
                qcell = f'<a data-l="{ln}" href="#L{ln}">{len(qs)} q</a>'
            rows.append(f'<div class="l{cls}" id="L{ln}"><a class="n" href="#L{ln}">{ln}</a><span class="c">{"" if c is None else c}</span>'
                        f'<span class="q">{qcell}</span><span>{html.escape(text)}</span></div>')
        attr_json = json.dumps(attr, separators=(',', ':')).replace('</', '<\\/')
        body = (f'<p><a href="{root}index.html">Overpass API corpus coverage</a> / {html.escape(fn)}</p>'
                f'<h1>{html.escape(fn)}</h1><p>{hit} of {len(counts)} instrumented lines run by the corpus '
                f'({pct(hit, len(counts))}) at <a href="{args.repo}/tree/{args.commit}">{short}</a> '
                f'(<a href="{args.repo}/blob/{args.commit}/src/{fn}">this file on GitHub</a>).</p>'
                f'<p class="legend muted"><span class="hit-k" style="background:var(--hit)">run</span>'
                f'<span style="background:var(--miss)">never run</span>columns: line, executions, queries '
                f'(click for the queries that run the line)</p>'
                f'<div class="scroll"><pre class="src">{"".join(rows)}</pre></div><div id="panel"></div>'
                f'<script type="application/json" id="attr">{attr_json}</script>'
                f'<script>const ROOT = "{root}";{FILE_JS}</script>')
        out = os.path.join(args.out, 'src', fn + '.html')
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, 'w') as f:
            f.write(page(fn, body, root))

    # Query pages.
    qdir = os.path.join(args.out, 'q')
    os.makedirs(qdir)
    for n, r in enumerate(corpus):
        uniq = sorted(unique.get(n, []))
        lst = ''.join(f'<li><a href="../src/{html.escape(f)}.html#L{ln}">{html.escape(f)}:{ln}</a></li>'
                      for f, ln in uniq[:200])
        more = f'<li class="muted">and {len(uniq) - 200} more</li>' if len(uniq) > 200 else ''
        body = (f'<p><a href="../index.html">Overpass API corpus coverage</a> / query {n}</p>'
                f'<h1>Query {n}</h1><p class="muted">{html.escape(r["id"])} · {r["cpu"]:.2f} s CPU · '
                f'expected status <code>{html.escape(r["status"])}</code></p>'
                f'<pre class="query">{html.escape(r["query"])}</pre>'
                f'<h2>Lines only this query runs ({len(uniq)})</h2>'
                + (f'<ul>{lst}{more}</ul>' if uniq else
                   '<p class="muted">None: it is in the corpus for edges or hit counts that other '
                   'queries reach only in part.</p>'))
        with open(os.path.join(qdir, f'{n}.html'), 'w') as f:
            f.write(page(f'Query {n}', body, '../'))

    # Index: by directory, then file.
    dirs = collections.defaultdict(lambda: [0, 0])
    for fn, (h, t) in stats.items():
        d = os.path.dirname(fn) or '.'
        dirs[d][0] += h
        dirs[d][1] += t
    all_hit = sum(h for h, _ in stats.values())
    all_lines = sum(t for _, t in stats.values())
    cpu = sum(r['cpu'] for r in corpus)
    rows = []
    for d in sorted(dirs):
        h, t = dirs[d]
        rows.append(f'<tr class="dir"><td>{html.escape(d)}/</td><td class="num">{h}</td><td class="num">{t}</td>'
                    f'<td class="num">{pct(h, t)}</td><td>{bar(h, t)}</td></tr>')
        for fn in sorted(f for f in stats if (os.path.dirname(f) or '.') == d):
            h, t = stats[fn]
            rows.append(f'<tr><td>&nbsp;&nbsp;<a href="src/{html.escape(fn)}.html">{html.escape(os.path.basename(fn))}'
                        f'</a></td><td class="num">{h}</td><td class="num">{t}</td><td class="num">{pct(h, t)}</td>'
                        f'<td>{bar(h, t)}</td></tr>')
    qrows = ''.join(f'<tr><td><a href="q/{n}.html">{n}</a></td><td class="num">{r["cpu"]:.2f}</td>'
                    f'<td class="num">{len(unique.get(n, []))}</td><td>{html.escape(r["status"][:60])}</td>'
                    f'<td class="muted">{html.escape(r["id"][:70])}</td></tr>' for n, r in enumerate(corpus))
    cpu_text = f'{cpu / 60:.1f} CPU minutes' if cpu >= 60 else f'{cpu:.0f} CPU seconds'
    body = (f'<h1>Overpass API: conformance corpus coverage</h1>'
            f'<p>Source: <a href="{args.repo}/tree/{args.commit}">{args.repo.split("github.com/")[-1]} at {short}</a>. '
            f'{len(corpus)} queries, {cpu_text} in all. They run '
            f'<b>{all_hit} of {all_lines}</b> instrumented lines ({pct(all_hit, all_lines)}) of the files the '
            f'query binary (<code>osm3s_query</code>) is built from.</p>{args.about}'
            f'<p><a href="corpus.jsonl">corpus.jsonl</a>: every query with its expected status and output digest.</p>'
            f'<h2>Files</h2><div class="scroll"><table><tr><th>File</th><th class="num">Lines run</th>'
            f'<th class="num">Instrumented</th><th class="num">%</th><th></th></tr>{"".join(rows)}</table></div>'
            f'<h2>Queries</h2><div class="scroll"><table><tr><th>#</th><th class="num">CPU s</th>'
            f'<th class="num">Unique lines</th><th>Expected status</th><th>Origin</th></tr>{qrows}</table></div>')
    with open(os.path.join(args.out, 'index.html'), 'w') as f:
        f.write(page('Overpass API corpus coverage', body, ''))
    print(f'{len(stats)} files, {all_hit}/{all_lines} lines, {len(corpus)} queries -> {args.out}')


if __name__ == '__main__':
    main()
