"""WP-04 / FC-10 + FC-11 - variable vs full product cost from the same run; cost-plus price, bid check.

CONTRACT
- Behaviour of a cost = ``cmc_behavior`` of its analytic line (fixed / variable). Each
  ``cmc.costing.quota`` splits into ``amount_fixed`` + ``amount_variable`` (= ``amount``): the center's
  fixed and variable pools are spread separately (same driver, same coefficient basis).
- ``cmc.costing.result.cost_variable`` = variable prime cost + variable indirect quotas + variable
  period cost; ``cost_fixed`` = ``cost_complessivo - cost_variable``; ``unit_cost_variable`` /
  ``unit_cost_fixed`` per produced unit. Σ ``cost_variable`` = all variable cost of the scope.
- ``cmc.costing.run.markup_percent`` (input) -> ``cmc.costing.result.price_cost_plus`` =
  ``unit_cost_complessivo * (1 + markup_percent / 100)`` rounded to the currency (Cinquini V.2: mark-up
  on the TOTAL cost, after commercial/administrative costs).
- ``cmc.costing.result._cmc_bid_check(price)`` -> dict ``{"unit_full_cost", "unit_variable_cost",
  "margin" (= price - unit full), "covers_full_cost", "covers_variable_cost"}`` (FC-11 bid vs cost).
Numbers: P1 variable 420 + 120 (Z 200 over 100 h) = 540, fixed 600 + 300 = 900, full 1440 ->
unit 5.40 / 9.00 / 14.40; P2 150 + 80 = 230, fixed 400 + 300 = 700, full 930 -> 4.60 / 14.00 / 18.60.
Mark-up 25 %: P1 18.00, P2 23.25. With a fixed period pool of 160 spread by units (P1 90 h, P2 70 h):
P1 full 1530 (unit 15.30, +20 % = 18.36), P2 1000 (unit 20.00, +20 % = 24.00).
"""

from odoo.tests.common import tagged

from .wp04_common import Wp04Case


@tagged("post_install", "-at_install")
class TestWp04VariableFull(Wp04Case):
    def test_fc10_variable_and_full_side_by_side(self):
        # FC-10: one model, two product costs (variable / full absorption)
        run = self._run_a()
        self.assertRecordValues(
            self._res(run, self.p1) | self._res(run, self.p2),
            [
                {"cost_variable": 540.0, "cost_fixed": 900.0, "cost_complessivo": 1440.0,
                 "unit_cost_variable": 5.40, "unit_cost_fixed": 9.00, "unit_cost_complessivo": 14.40},
                {"cost_variable": 230.0, "cost_fixed": 700.0, "cost_complessivo": 930.0,
                 "unit_cost_variable": 4.60, "unit_cost_fixed": 14.00, "unit_cost_complessivo": 18.60},
            ],
        )

    def test_fc10_quota_split_fixed_variable(self):
        # FC-10: the split is traceable per center quota
        run = self._run_a()
        quotas = self._res(run, self.p1).quota_ids
        self.assertRecordValues(
            quotas.filtered(lambda q: q.center_id == self.z) | quotas.filtered(lambda q: q.center_id == self.w),
            [
                {"amount": 720.0, "amount_fixed": 600.0, "amount_variable": 120.0},
                {"amount": 300.0, "amount_fixed": 300.0, "amount_variable": 0.0},
            ],
        )

    def test_fc10_variable_view_conserves(self):
        # FC-10: Σ variable product cost = variable cost booked (direct 570 + Z variable 200)
        run = self._run_a()
        self.assertAlmostEqual(sum(run.result_ids.mapped("cost_variable")), 770.0, places=2)
        self.assertAlmostEqual(sum(run.result_ids.mapped("cost_fixed")), 1600.0, places=2)

    def test_fc11_cost_plus_price(self):
        # FC-11 / Cinquini V.2: price = total cost x (1 + mark-up)
        run = self._run_a(markup_percent=25.0)
        self.assertRecordValues(
            self._res(run, self.p1) | self._res(run, self.p2),
            [{"price_cost_plus": 18.00}, {"price_cost_plus": 23.25}],
        )

    def test_fc11_cost_plus_on_total_cost_with_period_costs(self):
        # FC-11: the mark-up base is the TOTAL cost (industrial + commercial/administrative)
        period = self.env_a["account.analytic.account"].create(
            {"name": "WP04 sales", "plan_id": self.plan.id, "cmc_is_period": True, "company_id": self.company_a.id}
        )
        self._indirect(self.env_a, period, 160, "fixed")
        run = self._run_a(markup_percent=20.0)
        self.assertRecordValues(
            self._res(run, self.p1) | self._res(run, self.p2),
            [
                {"cost_comm_admin": 90.0, "cost_complessivo": 1530.0, "unit_cost_complessivo": 15.30,
                 "cost_variable": 540.0, "cost_fixed": 990.0, "price_cost_plus": 18.36},
                {"cost_comm_admin": 70.0, "cost_complessivo": 1000.0, "unit_cost_complessivo": 20.00,
                 "cost_variable": 230.0, "cost_fixed": 770.0, "price_cost_plus": 24.00},
            ],
        )

    def test_fc11_bid_check_against_cost(self):
        # FC-11: a bid below full cost but above variable cost is flagged, not hidden
        res = self._res(self._run_a(), self.p1)
        below_full = res._cmc_bid_check(12.0)
        self.assertAlmostEqual(below_full["unit_full_cost"], 14.40, places=2)
        self.assertAlmostEqual(below_full["unit_variable_cost"], 5.40, places=2)
        self.assertAlmostEqual(below_full["margin"], -2.40, places=2)
        self.assertFalse(below_full["covers_full_cost"])
        self.assertTrue(below_full["covers_variable_cost"])
        self.assertFalse(res._cmc_bid_check(5.0)["covers_variable_cost"])
        above = res._cmc_bid_check(15.0)
        self.assertTrue(above["covers_full_cost"])
        self.assertAlmostEqual(above["margin"], 0.60, places=2)
