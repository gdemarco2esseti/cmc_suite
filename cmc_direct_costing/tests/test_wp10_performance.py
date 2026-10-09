"""WP-10 / AD-114 - the Compute cost of a decision does not grow with its lines.

CONTRACT
- A quote / make-or-buy reads the rates of all its activity lines in one batch (one ``cmc.rate`` read for the
  whole decision, not ``_cmc_lookup`` per line), records the usages in one call and writes the snapshots
  in batch; a mix decision ranks in Python and creates its results with one ``create(vals_list)``; an
  investment creates its cash-flow results in one batch. 30 lines cost the same queries as 3 (small
  constant tolerance): a per-line search/create = N+1 = red.
"""

from odoo.tests.common import tagged

from odoo.addons.cmc_base.models.cmc_run_mixin import CMC_RUN_STATE

from .wp10_common import D_RATE, Wp10Case

TOLERANCE = 5


@tagged("post_install", "-at_install")
class TestWp10Performance(Wp10Case):
    def _quote(self, n):
        centers = self.env["account.analytic.account"].create([
            {"name": "WP10 vol %s-%s" % (n, i), "plan_id": self.plan.id, "cmc_center_type": "final"} for i in range(n)
        ])
        self.env["cmc.rate"].with_context(cmc_run_state=CMC_RUN_STATE)._cmc_publish(
            self.producer, [{"holder": center, "driver": self.mh, "date": D_RATE, "value": 10.0 + i} for i, center in enumerate(centers)]
        )
        return self._decision(
            "special_order", quote_price=1.0e6,
            line_ids=[self._line("own %s" % i, "cost", 0, 10.0) for i in range(n)],
            activity_ids=[self._act(center, self.mh, 1) for center in centers],
        )

    def _mix(self, n):
        return self._decision("mix", resource_available=10.0 * n, mix_line_ids=[
            (0, 0, {"name": "P%s" % i, "price_unit": 20 + i, "variable_unit": 10, "resource_per_unit": 1 + i % 3, "max_demand": 5})
            for i in range(n)
        ])

    def _investment(self, n):
        return self._decision("investment", discount_rate=8.0, cash_flow_ids=[
            (0, 0, {"year": year, "amount": -1000.0 if not year else 150.0}) for year in range(n)
        ])

    def _assert_flat(self, small, big):
        self.env.flush_all()
        self.env.invalidate_all()
        start = self.cr.sql_log_count
        small.action_compute()
        self.env.flush_all()
        budget = self.cr.sql_log_count - start + TOLERANCE
        self.env.invalidate_all()
        with self.assertQueryCount(budget):
            big.action_compute()
            self.env.flush_all()

    def test_quote_compute_independent_of_lines(self):
        # DIF-04 / AD-114: a 30-line quote prices with the same queries as a 3-line one
        self._assert_flat(self._quote(3), self._quote(30))

    def test_mix_compute_independent_of_lines(self):
        # DC-03 / AD-114: ranking 30 products costs the same queries as 3
        self._assert_flat(self._mix(3), self._mix(30))

    def test_investment_compute_independent_of_horizon(self):
        # DIF-06 / AD-114: a 30-year table costs the same queries as a 3-year one
        self._assert_flat(self._investment(3), self._investment(30))
