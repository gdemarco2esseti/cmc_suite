"""AD-114 - access cost of the variance engine and of the roll-up does not grow with the data (volume fixtures).

CONTRACT
- Variance Compute reads MP/MOD analytic lines (unbounded) and the finished moves with grouped reads at the
  MO x component / MO x center grain, and creates its results in batches: its query count for 6 MOs equals the one
  for 1 MO (small constant tolerance). A per-MO search/browse of lines = N+1 = red.
- Roll-up walks the BoM tree (bounded master data, AD-114 (6)) but reads planned prices, rate lines and existing
  cards once per roll-up and creates cards/lines in batches: 3-level trees of 15 products cost the same queries as 3.
"""

from odoo.tests.common import tagged

from .common import StdCase

TOLERANCE = 4


@tagged("post_install", "-at_install")
class TestStdPerformance(StdCase):
    def _measure(self, func):
        self.env.flush_all()
        self.env.invalidate_all()
        start = self.cr.sql_log_count
        func()
        self.env.flush_all()
        return self.cr.sql_log_count - start

    def test_variance_compute_independent_of_mos(self):
        # AD-114: a month of a real plant has hundreds of MOs; 6 must cost the queries of 1
        version = self._std_version()
        small = self._run(version, compute=False)
        budget = self._measure(small.action_compute) + TOLERANCE
        small.action_reset_to_draft()
        for _i in range(4):
            self._done_mo(self.f, self.bom_f, 2.0, {self.a: 5.0, self.b: 6.0}, duration=60.0)
        self._done_mo(self.g, self.bom_g, 1.0, duration=6.0, center=self.y)
        big = self._run(version, compute=False, name="STD big")
        small.unlink()
        self.env.flush_all()
        self.env.invalidate_all()
        with self.assertQueryCount(budget):
            big.action_compute()
        # the volume fixture is correct too: 6 MOs of the period, F lines a 4 kg-per-MO std of A
        self.assertEqual(len(big.line_ids.production_id), 6)
        line = big.line_ids.filtered(lambda vl: vl.line_type == "material" and vl.component_id == self.a and vl.output_qty == 2.0)[:1]
        self.assertAlmostEqual(line.std_qty, 4.0)
        self.assertAlmostEqual(line.quantity_variance, (4.0 - 5.0) * 4.0)

    def _tree(self, n, tag):
        """n top products, each = 1 sub-assembly (= 2 A + op WC1) + 1 B; sub-assembly = 1 leaf kit (= 1 C)."""
        tops = self.env["product.product"]
        for i in range(n):
            leaf = self._product("STD %s leaf %s" % (tag, i))
            self._bom(leaf, [(self.c, 1)])
            sub = self._product("STD %s sub %s" % (tag, i))
            self._bom(sub, [(leaf, 1), (self.a, 2)], [(self.wc1, 6.0)])
            top = self._product("STD %s top %s" % (tag, i))
            self._bom(top, [(sub, 1), (self.b, 1)])
            tops |= top
        return tops

    def test_rollup_independent_of_products(self):
        # AD-114: the roll-up of a whole catalogue must not cost one query batch per product
        small_tops, big_tops = self._tree(1, "small"), self._tree(5, "big")
        version = self._std_version(approve=False, products=self.env["product.product"])
        budget = self._measure(lambda: version.action_cmc_std_rollup(small_tops)) + TOLERANCE
        with self.assertQueryCount(budget):
            version.action_cmc_std_rollup(big_tops)
        self.assertEqual(len(version.card_ids), 3 + 15)
        top = self._card(version, big_tops[:1])
        # top = sub (leaf 1.5 + 2 A x 4 + 0.1 h x (20 + 16)) + 1 B x 2 = 1.5 + 8 + 3.6 + 2
        self.assertAlmostEqual(top.unit_cost, 15.1)
