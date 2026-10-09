"""BUD-01 / BUD-02 / INT-07 - budget lines on a plan version: sign, phasing, center budget, totals, lock.

Hand-computed (Kaplan Ch.3 App, center #55): fixed 95,300 phased equally -> 8 months 7,941.67 + 4 months
7,941.66 (largest remainder); variable 262,200 phased on planned machine hours Jan-Nov 950 h, Dec 900 h
(sum 11,350) -> Dec = 262,200 x 900 / 11,350 = 20,791.19; var rate = 262,200 / 11,350 = 23.10;
center budget = 95,300 + 262,200 = 357,500. Seasonality Jan-Jun weight 1, Jul-Dec weight 3 on 120,000 ->
5,000 / 15,000 per month. INT-07: 1,000 EUR at 1.1 company units per EUR -> -1,100 in company currency.
"""

from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import tagged

from .common import CmcBudgetCase, d


@tagged("post_install", "-at_install")
class TestBudgetLines(CmcBudgetCase):
    """BUD-01: the master budget is center x element x F/V x month, stored on a plan version."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.center = cls._center("BUD #55 machining")
        cls.version = cls._version()

    def test_cost_figure_stored_with_natural_sign(self):
        # AD-112: a cost typed as a positive figure is stored negative, a revenue positive
        cost = self._line(self.version, self.center, 3, 1000.0)
        revenue = self._line(self.version, False, 3, 2500.0, kind="revenue", element=self.rent)
        self.assertRecordValues(cost | revenue, [
            {"figure": 1000.0, "amount": -1000.0, "amount_company": -1000.0, "date": d(3)},
            {"figure": 2500.0, "amount": 2500.0, "amount_company": 2500.0, "date": d(3)},
        ])

    def test_line_date_normalised_to_month_start(self):
        # AD-105: the month bucket is the first day of the month
        line = self.Line.create({"version_id": self.version.id, "center_id": self.center.id,
                                 "element_id": self.rent.id, "date": d(3, 17), "figure": 10.0})
        self.assertEqual(line.date, d(3))

    def test_line_outside_version_span_refused(self):
        # AD-104: a plan figure belongs to the span of its version
        with self.assertRaises(ValidationError):
            self._line(self.version, self.center, 3, 10.0, date=d(3, year=2034))

    def test_cost_line_requires_center(self):
        # BUD-01: a cost budget is always given to a center
        with self.assertRaises(ValidationError):
            self._line(self.version, False, 3, 10.0)

    def test_fixed_budget_phased_equally_and_conserved(self):
        # BUD-02: fixed = annual / 12, the cents re-sum to the annual figure
        lines = self.version._cmc_budget_add(
            {"center_id": self.center.id, "element_id": self.rent.id, "behavior": "fixed"}, 95300.0
        )
        self.assertEqual(len(lines), 12)
        self.assertEqual(sorted(set(lines.mapped("date"))), self.version._cmc_months())
        self.assertAlmostEqual(sum(lines.mapped("figure")), 95300.0, places=2)
        self.assertEqual(sorted(lines.mapped("figure")).count(7941.66), 4)
        self.assertEqual(sorted(lines.mapped("figure")).count(7941.67), 8)

    def test_fixed_budget_ignores_seasonality(self):
        # BUD-02: seasonality shapes variable costs and revenues, never the fixed cost
        profile = self._season()
        lines = self.version._cmc_budget_add(
            {"center_id": self.center.id, "element_id": self.rent.id, "behavior": "fixed"}, 120000.0, seasonality=profile
        )
        self.assertEqual(set(lines.mapped("figure")), {10000.0})

    def test_revenue_phased_by_seasonality(self):
        # BUD-02: month_m = annual x season%_m
        lines = self.version._cmc_budget_add({"kind": "revenue", "behavior": "variable"}, 120000.0, seasonality=self._season())
        by_month = {line.date.month: line.figure for line in lines}
        self.assertEqual(by_month, {**dict.fromkeys(range(1, 7), 5000.0), **dict.fromkeys(range(7, 13), 15000.0)})
        self.assertEqual(set(lines.mapped("amount")), {5000.0, 15000.0})

    def test_variable_budget_phased_on_planned_driver(self):
        # BUD-01 / AD-107: variable cost follows the planned driver quantities of the version
        self._plan_machine_hours()
        lines = self.version._cmc_budget_add(
            {"center_id": self.center.id, "element_id": self.energy.id, "behavior": "variable", "driver_id": self.mh.id},
            262200.0,
        )
        self.assertAlmostEqual(sum(lines.mapped("figure")), 262200.0, places=2)
        dec = lines.filtered(lambda line: line.date == d(12))
        self.assertAlmostEqual(dec.figure, 20791.19, places=2)
        self.assertEqual(set(lines.mapped("driver_id")), {self.mh})

    def test_center_budget_is_fixed_plus_rate_times_planned_driver(self):
        # BUD-01 (Kaplan #55): 95,300 + 262,200 = 357,500 and var rate 262,200 / 11,350 = 23.10
        self._plan_machine_hours()
        self.version._cmc_budget_add({"center_id": self.center.id, "element_id": self.rent.id, "behavior": "fixed"}, 95300.0)
        self.version._cmc_budget_add(
            {"center_id": self.center.id, "element_id": self.energy.id, "behavior": "variable", "driver_id": self.mh.id},
            262200.0,
        )
        budget = self.version._cmc_center_budget()[self.center.id]
        self.assertAlmostEqual(budget["fixed"], 95300.0, places=2)
        self.assertAlmostEqual(budget["variable"], 262200.0, places=2)
        self.assertAlmostEqual(budget["total"], 357500.0, places=2)
        self.assertAlmostEqual(self.version._cmc_variable_rate(self.center, self.mh), 23.10, places=2)

    def test_variable_rate_zero_without_planned_quantity(self):
        # BUD-01: no planned driver, no rate (never a division by zero)
        self._line(self.version, self.center, 3, 500.0, element=self.energy, behavior="variable", driver_id=self.mh.id)
        self.assertEqual(self.version._cmc_variable_rate(self.center, self.mh), 0.0)

    def test_company_total_checked_on_approve(self):
        # BUD-01: the sum of the lines must be the company total typed on the version
        self.version.target_total_cost = 12000.0
        self._line(self.version, self.center, 1, 11000.0)
        with self.assertRaises(UserError):
            self.version.with_user(self.manager).action_approve()
        self._line(self.version, self.center, 2, 1000.0)
        self._approve(self.version)
        self.assertEqual(self.version.state, "done")

    def test_approved_version_locks_its_lines(self):
        # AD-104: approved = locked inputs
        line = self._line(self.version, self.center, 3, 100.0)
        self._approve(self.version)
        with self.assertRaises(UserError):
            line.figure = 200.0
        with self.assertRaises(UserError):
            self._line(self.version, self.center, 4, 100.0)
        with self.assertRaises(UserError):
            line.unlink()

    def test_reset_reopens_the_lines(self):
        # AD-104: only Reset to Draft (manager) reopens an approved budget
        line = self._line(self.version, self.center, 3, 100.0)
        self._approve(self.version)
        self.version.with_user(self.manager).action_reset_to_draft()
        line.figure = 200.0
        self.assertEqual(line.amount, -200.0)

    def test_version_currency_converted_at_fixed_rate(self):
        # INT-07: budget in version currency, fixed internal rate to the company currency
        version = self._version(name="BUD foreign", currency_id=self._foreign().id, currency_rate=1.1)
        line = self._line(version, self.center, 3, 1000.0)
        self.assertRecordValues(line, [{"amount": -1000.0, "amount_company": -1100.0, "currency_id": self._foreign().id}])

    def test_rate_change_reconverts_draft_lines(self):
        # INT-07: the company amount follows the version rate while the version is a draft
        version = self._version(name="BUD foreign 2", currency_id=self._foreign().id, currency_rate=1.1)
        line = self._line(version, self.center, 3, 1000.0)
        version.currency_rate = 0.8
        self.assertAlmostEqual(line.amount_company, -800.0)

    def test_seasonality_month_must_be_valid(self):
        # BUD-02: a profile has at most one weight per calendar month 1..12
        with self.assertRaises(ValidationError):
            self.env["cmc.budget.seasonality"].create({"name": "bad", "line_ids": [(0, 0, {"month": 13, "weight": 1.0})]})
        with self.assertRaises(ValidationError):
            self.env["cmc.budget.seasonality"].create({"name": "dup", "line_ids": [
                (0, 0, {"month": 1, "weight": 1.0}), (0, 0, {"month": 1, "weight": 2.0}),
            ]})

    # ---- helpers --------------------------------------------------------

    def _season(self):
        return self.env["cmc.budget.seasonality"].create({
            "name": "BUD summer peak",
            "line_ids": [(0, 0, {"month": m, "weight": 1.0 if m <= 6 else 3.0}) for m in range(1, 13)],
        })

    def _plan_machine_hours(self):
        for month in range(1, 13):
            self._qty(self.mh, self.center, month, 900.0 if month == 12 else 950.0, version=self.version)

    def _foreign(self):
        currency = self.env.ref("base.EUR") if self.company.currency_id != self.env.ref("base.EUR") else self.env.ref("base.CHF")
        currency.active = True
        return currency
