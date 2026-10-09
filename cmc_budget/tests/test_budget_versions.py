"""FCS-01 / BUD-03 - budget -> forecast copy, rolling forecast, multi-year business plan.

Hand-computed rolling forecast (cutoff 28 Feb 2033): budget rent 1,000/month (12,000). Actuals rent Jan 900,
Feb 1,100, energy Jan 50 (not budgeted), rent Mar 777 (after the cutoff, ignored) ->
forecast = 900 + 1,100 + 50 + 10 x 1,000 = 12,050; revising March to 1,500 -> 12,550, budget still 12,000.
Business plan 2033-2035: revenue 360,000 (10,000/month), fixed cost 270,000 (7,500/month), target profit
100,000 -> profit 90,000, gap 10,000; split into 3 annual versions of 120,000 revenue each.
"""

from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .common import CmcBudgetCase, d


@tagged("post_install", "-at_install")
class TestBudgetVersions(CmcBudgetCase):
    """FCS-01: a forecast is a version comparable with the budget; BUD-03: a plan splits into annual budgets."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.center = cls._center("BUD forecast center")
        cls.budget = cls._version(name="BUD 2033 master")
        cls.budget._cmc_budget_add({"center_id": cls.center.id, "element_id": cls.rent.id, "behavior": "fixed"}, 12000.0)

    def _cost_total(self, version):
        return sum(version.budget_line_ids.filtered(lambda line: line.kind == "cost").mapped("figure"))

    def test_copy_budget_to_forecast_duplicates_lines(self):
        # FCS-01 / AD-104: the forecast starts as a draft copy of the budget
        self._approve(self.budget)
        forecast = self.budget.copy({"version_type": "forecast"})
        self.assertRecordValues(forecast, [{"version_type": "forecast", "state": "draft", "parent_id": self.budget.id}])
        self.assertEqual(len(forecast.budget_line_ids), 12)
        self.assertAlmostEqual(self._cost_total(forecast), 12000.0)
        self.assertFalse(forecast.budget_line_ids & self.budget.budget_line_ids)

    def test_forecast_edit_leaves_approved_budget_untouched(self):
        # FCS-01: revising the forecast never changes the approved budget
        self._approve(self.budget)
        forecast = self.budget.copy({"version_type": "forecast"})
        forecast.budget_line_ids.filtered(lambda line: line.date == d(6)).figure = 4000.0
        self.assertAlmostEqual(self._cost_total(forecast), 15000.0)
        self.assertAlmostEqual(self._cost_total(self.budget), 12000.0)

    def test_rolling_forecast_is_actual_ytd_plus_remaining_budget(self):
        # FCS-01: forecast = actual_1..m + revised_m+1..12
        self._actuals()
        forecast = self.budget.with_user(self.manager)._cmc_rolling_forecast(d(2, 28)).sudo()
        self.assertRecordValues(forecast, [{"version_type": "forecast", "state": "draft", "parent_id": self.budget.id,
                                            "date_from": d(1), "date_to": d(12, 31)}])
        lines = forecast.budget_line_ids
        jan_feb = lines.filtered(lambda line: line.date <= d(2))
        self.assertEqual(
            sorted((line.date, line.element_id, line.figure) for line in jan_feb),
            sorted([(d(1), self.rent, 900.0), (d(1), self.energy, 50.0), (d(2), self.rent, 1100.0)]),
        )
        self.assertEqual(set(lines.filtered(lambda line: line.date > d(2)).mapped("figure")), {1000.0})
        self.assertAlmostEqual(self._cost_total(forecast), 12050.0)

    def test_rolling_forecast_keeps_behavior_and_center_of_actuals(self):
        # FCS-01: actual months are keyed like the budget (center x element x behavior)
        self._actuals()
        forecast = self.budget.with_user(self.manager)._cmc_rolling_forecast(d(2, 28)).sudo()
        energy = forecast.budget_line_ids.filtered(lambda line: line.element_id == self.energy)
        self.assertRecordValues(energy, [{"center_id": self.center.id, "behavior": "variable", "amount": -50.0}])

    def test_rolling_forecast_remainder_is_revisable(self):
        # FCS-01: the remainder is the revised part, editable while the forecast is a draft
        self._actuals()
        forecast = self.budget.with_user(self.manager)._cmc_rolling_forecast(d(2, 28)).sudo()
        forecast.budget_line_ids.filtered(lambda line: line.date == d(3)).figure = 1500.0
        self.assertAlmostEqual(self._cost_total(forecast), 12550.0)
        self.assertAlmostEqual(self._cost_total(self.budget), 12000.0)

    def test_multi_year_plan_split_into_annual_budgets(self):
        # BUD-03: a 3-year business plan produces one budget per year
        plan = self._business_plan()
        years = plan.action_split_annual()
        self.assertEqual(len(years), 3)
        self.assertEqual(sorted(years.mapped("date_from")), [d(1, year=2033), d(1, year=2034), d(1, year=2035)])
        self.assertEqual(set(years.mapped("parent_id")), {plan})
        self.assertEqual(set(years.mapped("state")), {"draft"})
        self.assertEqual(set(years.mapped("version_type")), {"budget"})
        for year in years:
            self.assertIn(str(year.date_from.year), year.name)
            revenue = year.budget_line_ids.filtered(lambda line: line.kind == "revenue")
            self.assertEqual(len(revenue), 12)
            self.assertAlmostEqual(sum(revenue.mapped("figure")), 120000.0)

    def test_split_of_a_single_year_refused(self):
        # BUD-03: nothing to split in a one-year version
        with self.assertRaises(UserError):
            self.budget.action_split_annual()

    def test_profit_gap_to_target(self):
        # BUD-03: profit = sales - costs, iterate until it matches the target
        gap = self._business_plan()._cmc_profit_gap()
        self.assertEqual({k: round(v, 2) for k, v in gap.items()},
                         {"revenue": 360000.0, "cost": 270000.0, "profit": 90000.0, "target": 100000.0, "gap": 10000.0})

    # ---- helpers --------------------------------------------------------

    def _actuals(self):
        self._cost(self.center, 900.0, d(1, 10))
        self._cost(self.center, 1100.0, d(2, 10))
        self._cost(self.center, 50.0, d(1, 20), element=self.energy, behavior="variable")
        self._cost(self.center, 777.0, d(3, 5))

    def _business_plan(self):
        plan = self._version(name="BUD plan 33-35", date_to=d(12, 31, year=2035), target_profit=100000.0)
        plan._cmc_budget_add({"kind": "revenue", "behavior": "variable"}, 360000.0)
        plan._cmc_budget_add({"center_id": self.center.id, "element_id": self.rent.id, "behavior": "fixed"}, 270000.0)
        return plan
