"""FCS-04 - monthly cash forecast from the budget and the payment terms (moved to WP-02 by AD-101/AD-118).

Hand-computed (version Jan-Mar 2033, documents dated the 15th): revenue 10,000/month, customer term 50%
immediate + 50% at 60 days; costs 6,000/month, supplier term 30 days; depreciation 1,000/month (no cash);
opening balance 1,000.
  month  inflow  outflow   net    cumulative
  Jan     5,000       0   5,000     6,000
  Feb     5,000  -6,000  -1,000     5,000
  Mar    10,000  -6,000   4,000     9,000
  Apr     5,000  -6,000  -1,000     8,000
  May     5,000       0   5,000    13,000
A revenue line with its own immediate term moves its whole amount into its month.
"""

from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .common import CmcBudgetCase, d


@tagged("post_install", "-at_install")
class TestBudgetCash(CmcBudgetCase):
    """FCS-04: net cash per month = inflows at customer terms - outflows at supplier terms."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Term = cls.env["account.payment.term"]
        cls.customer_term = Term.create({"name": "BUD 50% now 50% 60d", "line_ids": [
            (0, 0, {"value": "percent", "value_amount": 50.0, "nb_days": 0}),
            (0, 0, {"value": "percent", "value_amount": 50.0, "nb_days": 60}),
        ]})
        cls.supplier_term = Term.create({"name": "BUD 30d", "line_ids": [
            (0, 0, {"value": "percent", "value_amount": 100.0, "nb_days": 30}),
        ]})
        cls.immediate = Term.create({"name": "BUD now", "line_ids": [
            (0, 0, {"value": "percent", "value_amount": 100.0, "nb_days": 0}),
        ]})
        cls.depreciation = cls.env["cmc.cost.element"].create({"name": "BUD Depreciation", "code": "BUDDEP", "cmc_cash_excluded": True})
        cls.center = cls._center("BUD cash center")
        cls.version = cls._version(name="BUD cash Q1", date_to=d(3, 31))
        for month in (1, 2, 3):
            cls._line(cls.version, False, month, 10000.0, kind="revenue", behavior="variable")
            cls._line(cls.version, cls.center, month, 6000.0, element=cls.rent)
            cls._line(cls.version, cls.center, month, 1000.0, element=cls.depreciation)

    def _run(self, version=None):
        run = self.env["cmc.budget.cash.run"].with_user(self.manager).create({
            "name": "BUD cash", "version_id": (version or self.version).id, "opening_balance": 1000.0,
            "customer_payment_term_id": self.customer_term.id, "supplier_payment_term_id": self.supplier_term.id,
        })
        run.action_compute()
        return run.sudo()

    def _table(self, run):
        return [(line.date, line.inflow, line.outflow, line.net, line.cumulative) for line in run.line_ids.sorted("date")]

    def test_cash_forecast_by_month(self):
        # FCS-04: net cash_m = inflows_m - outflows_m, cumulated on the opening balance
        self._approve(self.version)
        self.assertEqual(self._table(self._run()), [
            (d(1), 5000.0, 0.0, 5000.0, 6000.0),
            (d(2), 5000.0, -6000.0, -1000.0, 5000.0),
            (d(3), 10000.0, -6000.0, 4000.0, 9000.0),
            (d(4), 5000.0, -6000.0, -1000.0, 8000.0),
            (d(5), 5000.0, 0.0, 5000.0, 13000.0),
        ])

    def test_line_payment_term_overrides_default(self):
        # FCS-04: a budget line may carry its own payment term
        self.version.budget_line_ids.filtered(lambda line: line.kind == "revenue" and line.date == d(1)).payment_term_id = self.immediate
        self._approve(self.version)
        table = {row[0]: row for row in self._table(self._run())}
        self.assertAlmostEqual(table[d(1)][1], 10000.0)
        self.assertAlmostEqual(table[d(3)][1], 5000.0)

    def test_non_cash_cost_excluded(self):
        # FCS-04: depreciation is a cost, not a payment
        self._approve(self.version)
        self.assertAlmostEqual(sum(self._run().line_ids.mapped("outflow")), -18000.0)

    def test_cash_run_requires_approved_version(self):
        # AD-113: the cash forecast reads an approved budget
        with self.assertRaises(UserError):
            self._run()

    def test_version_locked_while_cash_forecast_computed(self):
        # AD-002: the budget read by a computed cash forecast cannot be reset
        self._approve(self.version)
        self._run()
        with self.assertRaises(UserError):
            self.version.with_user(self.manager).action_reset_to_draft()
