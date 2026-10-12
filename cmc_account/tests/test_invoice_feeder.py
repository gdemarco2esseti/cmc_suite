"""AD-107 - cmc_account declares the customer invoice-line count feeder (wave A0).

CONTRACT
- Data: driver ``cmc_account.cmc_measure_customer_invoice_lines`` (``cmc.cost.driver``, ``company_id``
  False, ``measure_kind`` = ``statistical``, ``measure_type`` = ``transaction``, ``source`` = ``feeder``,
  ``feeder_key`` = ``account_customer_invoice_lines``).
- ``cmc.measure.quantity._cmc_feeder_profiles()`` includes the key ``account_customer_invoice_lines``.
- After ``_cmc_refresh_feeders(date_from, date_to, company=..., keys=[...])``: one actual fact per
  (customer = ``account.move.line.partner_id``, month, company) with ``quantity`` = number of product
  lines (``display_type = 'product'``) of POSTED customer invoices (``move_type = 'out_invoice'``; credit
  notes are not invoice lines); ``cost_object_id`` = ``customer`` object of the partner; ``center_id``
  empty; ``feeder_key`` set; idempotent; cancelled invoices disappear at the next refresh.
"""

from datetime import date

from odoo import Command
from odoo.tests.common import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon

KEY = "account_customer_invoice_lines"


@tagged("post_install", "-at_install")
class TestInvoiceLineFeeder(AccountTestInvoicingCommon):
    """AD-107/INT-02: invoice-line counts per customer feed the driver catalogue."""

    @classmethod
    def get_default_groups(cls):
        # why: AccountTestInvoicingCommon runs as `accountman`; the readers/feeders are CMC-gated
        return super().get_default_groups() | cls.env.ref("cmc_base.group_cmc_manager")

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Fact = cls.env["cmc.measure.quantity"]
        cls.company = cls.company_data["company"]
        cls.company_data_b = cls.setup_other_company(name="CMC feeder B")

    @classmethod
    def _invoice(cls, partner, day, lines=1, move_type="out_invoice", data=None, post=True):
        data = data or cls.company_data
        move = cls.env["account.move"].with_company(data["company"]).create({
            "move_type": move_type,
            "partner_id": partner.id,
            "invoice_date": day,
            "date": day,
            "journal_id": data["default_journal_sale"].id,
            "invoice_line_ids": [
                Command.create({
                    "name": "line", "product_id": cls.product_a.id, "quantity": 1.0, "price_unit": 10.0,
                    "account_id": data["default_account_revenue"].id, "tax_ids": [Command.clear()],
                })
                for _i in range(lines)
            ],
        })
        if post:
            move.action_post()
        return move

    def _refresh(self, company=None):
        self.Fact._cmc_refresh_feeders(date(2026, 3, 1), date(2026, 3, 31), company=company or self.company, keys=[KEY])

    def _counts(self, company=None):
        facts = self.Fact.search([("feeder_key", "=", KEY), ("company_id", "=", (company or self.company).id)])
        return {self.env[f.cost_object_id.res_model].browse(f.cost_object_id.res_id): f.quantity for f in facts}

    def test_profile_and_driver_declared(self):
        # AD-107: cmc_account declares invoice-line counts per customer
        driver = self.env.ref("cmc_account.cmc_measure_customer_invoice_lines")
        self.assertRecordValues(
            driver,
            [{"source": "feeder", "feeder_key": KEY, "measure_kind": "statistical", "measure_type": "transaction",
              "company_id": False}],
        )
        self.assertIn(KEY, [profile["key"] for profile in self.Fact._cmc_feeder_profiles()])

    def test_lines_counted_per_customer(self):
        # INT-02: driver quantity = invoice lines per customer and month
        self._invoice(self.partner_a, date(2026, 3, 5), lines=2)
        self._invoice(self.partner_a, date(2026, 3, 25), lines=1)
        self._invoice(self.partner_b, date(2026, 3, 10), lines=1)
        self._invoice(self.partner_b, date(2026, 3, 11), lines=4, post=False)  # draft
        self._invoice(self.partner_a, date(2026, 3, 12), lines=1, move_type="out_refund")  # not an invoice line
        self._invoice(self.partner_a, date(2026, 4, 1), lines=1)  # outside the month
        self._refresh()
        self.assertEqual(self._counts(), {self.partner_a: 3.0, self.partner_b: 1.0})
        fact = self.Fact.search([("feeder_key", "=", KEY), ("company_id", "=", self.company.id)], limit=1)
        self.assertEqual(fact.cost_object_id.object_type, "customer")
        self.assertEqual(fact.date, date(2026, 3, 1))
        self.assertFalse(fact.version_id)
        self.assertFalse(fact.center_id)

    def test_refresh_idempotent_and_follows_cancellation(self):
        # AD-107: replace, never append; AD-111 inv.(2): documents can be cancelled
        self._invoice(self.partner_a, date(2026, 3, 5), lines=2)
        cancelled = self._invoice(self.partner_a, date(2026, 3, 6), lines=1)
        self._refresh()
        self._refresh()
        self.assertEqual(self._counts(), {self.partner_a: 3.0})
        cancelled.button_draft()
        cancelled.button_cancel()
        self._refresh()
        self.assertEqual(self._counts(), {self.partner_a: 2.0})

    def test_company_isolation(self):
        # AD-115: facts per company, from that company's invoices only
        self._invoice(self.partner_a, date(2026, 3, 5), lines=1)
        self._invoice(self.partner_a, date(2026, 3, 5), lines=5, data=self.company_data_b)
        self._refresh()
        self._refresh(company=self.company_data_b["company"])
        self.assertEqual(self._counts(), {self.partner_a: 1.0})
        self.assertEqual(self._counts(self.company_data_b["company"]), {self.partner_a: 5.0})
