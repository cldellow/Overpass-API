#!/usr/bin/env python3
"""Tests for wr_union.wr_as_union (no Overpass build needed): python3 bench/test_wr_union.py"""

import unittest

from wr_union import wr_as_union


class WrAsUnion(unittest.TestCase):
    def test_unchanged_without_wr_bn(self):
        for q in ('node(1); out;', 'wr(r.s)->.x;', 'nwr["a"]; out;', 'way(bn.s)->.w;'):
            self.assertEqual(wr_as_union(q), q)

    def test_named_input_and_target(self):
        self.assertEqual(
            wr_as_union('node->.n; wr(bn.n)["a"="b"]->.s4; .s4 out;'),
            'node->.n; ._->.wrbnin; way(bn.n)["a"="b"]->.wrbnway; .wrbnin->._; '
            'rel(bn.n)["a"="b"]->.wrbnrel; .wrbnin->._; (.wrbnway; .wrbnrel;)->.s4; .s4 out;')

    def test_implicit_input_writes_underscore(self):
        self.assertEqual(
            wr_as_union('node; wr(bn)[highway]; out;'),
            'node; ._->.wrbnin; way(bn)[highway]->.wrbnway; .wrbnin->._; '
            'rel(bn)[highway]->.wrbnrel; .wrbnin->._; (.wrbnway; .wrbnrel;)->._; out;')

    def test_input_set_prefix(self):
        self.assertIn('way.y(bn.n)->.wrbnway', wr_as_union('wr.y(bn.n);'))

    def test_role_and_quoted_semicolon(self):
        r = wr_as_union('wr(bn.s:"a;b")["x"="y;z"]->.t;')
        self.assertIn('way(bn.s:"a;b")["x"="y;z"]->.wrbnway', r)
        self.assertIn('rel(bn.s:"a;b")["x"="y;z"]->.wrbnrel', r)

    def test_nested_statements_are_left_alone(self):
        for q in ('node->.n; (wr(bn.n)[highway];); out;',
                  'node->.n; (way(1); wr(bn.n);); out;',
                  'node->.c; foreach.c->.it { wr(bn.it); out; }'):
            self.assertEqual(wr_as_union(q), q)

    def test_statement_after_a_block(self):
        r = wr_as_union('node->.n; complete(2).n->.c { node(w); way(bn)["a"]; }\nwr(bn.n)["b"]->.s3;')
        self.assertIn('}\n._->.wrbnin; way(bn.n)["b"]->.wrbnway', r)
        self.assertIn('(.wrbnway; .wrbnrel;)->.s3', r)

    def test_all_or_nothing(self):
        q = 'node->.n; wr(bn.n)->.a; (wr(bn.n);); out;'
        self.assertEqual(wr_as_union(q), q)


if __name__ == '__main__':
    unittest.main()
