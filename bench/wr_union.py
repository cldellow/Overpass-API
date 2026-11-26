"""Rewrite `wr(bn...)` query statements as the union of their way and relation halves.

`wr` is defined as `way` plus `relation` with the same filters. `master` (upstream) gets
`wr(bn.s)` with filters wrong: it returns nothing at the current date, unrelated relations at
past dates, or fails with "Filters too weak" (fork issue #192, fixed by #193). Its `way(bn.s)`
and `rel(bn.s)` are right. So when a candidate build differs from the baseline on a query with
such a statement, the baseline's answer to the rewritten query is what the candidate must give:
fuzz.py and props.py accept the difference only if the candidate's output equals it. A
regression in the candidate's `wr(bn)` still fails.

Only statements at the top level (not inside a union, difference or block) are rewritten, since
the expansion assigns sets. A query whose `wr(bn` statements can't all be rewritten is returned
unchanged, so it is never accepted.
"""

import re

TEMP_IN, TEMP_WAY, TEMP_REL = 'wrbnin', 'wrbnway', 'wrbnrel'
_STATEMENT = re.compile(r'wr(?=[(.\[])')


def _statements(query):
    """Split query into (text, depth) segments at ';' outside strings; depth is the number of
    open '(' or '{' at the segment's start."""
    segments, start, depth, seg_depth, quote, i = [], 0, 0, 0, None, 0
    while i < len(query):
        c = query[i]
        if quote:
            if c == '\\':
                i += 1
            elif c == quote:
                quote = None
        elif c in '"\'':
            quote = c
        elif c in '({':
            depth += 1
        elif c in ')}':
            depth -= 1
        elif c == ';':
            segments.append((query[start:i], seg_depth))
            start, seg_depth = i + 1, depth
        i += 1
    segments.append((query[start:], seg_depth))
    return segments


def _split_target(statement):
    """(body, target) where target is the '->.name' set the statement writes to, or None."""
    depth, quote, last = 0, None, None
    i = 0
    while i < len(statement):
        c = statement[i]
        if quote:
            if c == '\\':
                i += 1
            elif c == quote:
                quote = None
        elif c in '"\'':
            quote = c
        elif c in '({[':
            depth += 1
        elif c in ')}]':
            depth -= 1
        elif depth == 0 and statement.startswith('->', i):
            last = i
        i += 1
    if last is None:
        return statement, None
    return statement[:last], statement[last + 2:].strip()


def wr_as_union(query):
    """The query with each top-level `wr...(bn...)...` statement replaced by the union of its
    way and relation halves, both reading the same `_`; the query unchanged if it has none, or if
    any `wr(bn` statement isn't at the top level."""
    if 'wr(bn' not in query and not re.search(r'wr\.\w+\(bn', query):
        return query
    out, changed = [], False
    segments = _statements(query)
    for n, (segment, depth) in enumerate(segments):
        # A segment after a block's last statement starts with the block's closing '}': the
        # statement that follows it is one level further out.
        stripped = segment.lstrip()
        while stripped.startswith('}'):
            depth -= 1
            stripped = stripped[1:].lstrip()
        lead = segment[:len(segment) - len(stripped)]
        if _STATEMENT.match(stripped) and '(bn' in stripped:
            body, target = _split_target(stripped)
            if depth != 0 or body.lstrip()[:1] in '({' or (target is not None and not target.startswith('.')):
                return query
            rest = body[2:]
            result = target if target is not None else '._'
            expansion = (f'._->.{TEMP_IN}; way{rest}->.{TEMP_WAY}; .{TEMP_IN}->._; '
                         f'rel{rest}->.{TEMP_REL}; .{TEMP_IN}->._; (.{TEMP_WAY}; .{TEMP_REL};)->{result}')
            out.append(lead + expansion)
            changed = True
        else:
            if 'wr(bn' in segment or re.search(r'wr\.\w+\(bn', segment):
                return query
            out.append(segment)
    return ';'.join(out) if changed else query
