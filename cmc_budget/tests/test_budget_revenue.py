"""BUD-04 / FCS-01 - revenue budget against the GL (AD-111: revenue = -balance of posted income items).

Hand-computed (March 2033): revenue budget 10,000; posted invoice 12,000 + posted credit note 1,000 + draft
invoice 5,000 (ignored) -> actual 11,000, variance +1,000 (favourable, +10%). Rolling forecast cut at
31 March: March revenue line = 11,000 actual, April keeps the 10,000 budget.
"""

from odoo import Command
from odoo.tests.common import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon

from .common import CmcBudgetCommon, d


@tagged("post_install", "-at_install")
class TestBudgetRevenue(CmcBudgetCommon, AccountTestInvoicingCommon):
    """BUD-04: the revenue budget is compared with the GL, credit notes netted, drafts ignored."""

    @classmethod
    def get_default_groups(cls):
        # why: AccountTestInvoicingCommon runs as `accountman`; budget and readers are CMC-gated
        return super().get_default_groups() | cls.env.ref("cmc_base.group_cmc_manager")

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._cmc_budget_setup(company=cls.company_data["company"])
        cls.version = cls._version(name="BUD revenue 2033")
        for month in (3, 4):
            cls._line(cls.version, False, month, 10000.0, kind="revenue", behavior="variable")
        cls._invoice(12000.0, d(3, 10))
        cls._invoice(1000.0, d(3, 20), move_type="out_refund")
        cls._invoice(5000.0, d(3, 25), post=False)
        cls.version.with_user(cls.manager).action_approve()

    @classmethod
    def _invoice(cls, amount, day, move_type="out_invoice", post=True):
        move = cls.env["account.move"].create({
            "move_type": move_type, "partner_id": cls.partner_a.id, "invoice_date": day, "date": day,
            "journal_id": cls.company_data["default_journal_sale"].id,
            "invoice_line_ids": [Command.create({
                "name": "BUD sale", "product_id": cls.product_a.id, "quantity": 1.0, "price_unit": amount,
                "account_id": cls.company_data["default_account_revenue"].id, "tax_ids": [Command.clear()],
            })],
        })
        if post:
            move.action_post()
        return move

    def test_revenue_actual_from_gl_net_of_credit_notes(self):
        # AD-111 / BUD-04: posted income items, credit notes net by sign, drafts out
        self.env.flush_all()
        row = self.env["cmc.budget.bva"].search([("version_id", "=", self.version.id), ("kind", "=", "revenue"), ("date", "=", d(3))])
        self.assertRecordValues(row, [{"plan_amount": 10000.0, "actual_amount": 11000.0, "variance": 1000.0, "center_id": False}])

    def test_revenue_summary_pct(self):
        # BUD-04: revenue above budget is favourable (+10%)
        rows = self.version.with_user(self.manager)._cmc_bva_summary(d(3, 31), groupby=("kind",))
        (revenue,) = [row for row in rows if row["kind"] == "revenue"]
        self.assertAlmostEqual(revenue["variance_month"], 1000.0)
        self.assertAlmostEqual(revenue["variance_month_pct"], 10.0)

    def test_rolling_forecast_takes_actual_revenue(self):
        # FCS-01: actual months replace the budgeted revenue, later months keep it
        forecast = self.version.with_user(self.manager)._cmc_rolling_forecast(d(3, 31)).sudo()
        revenue = forecast.budget_line_ids.filtered(lambda line: line.kind == "revenue")
        self.assertEqual(sorted((line.date, line.amount) for line in revenue), [(d(3), 11000.0), (d(4), 10000.0)])
