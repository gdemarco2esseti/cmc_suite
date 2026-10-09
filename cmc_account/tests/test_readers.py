"""AD-111 / AD-112 - revenue and GL readers in cmc_account (wave A0).

CONTRACT (one reader per source, on ``account.move.line``, all ``api.model``):
- ``_cmc_revenue_read_group(company, date_from, date_to, groupby=())`` -> list of tuples
  ``(*group values as _read_group returns them, revenue)``; with no groupby ``[(revenue,)]``.
  Source: journal items with ``parent_state = 'posted'``, ``company_id = company``, account type
  ``income`` or ``income_other``, ``date`` in the span, ALL move types (credit notes and misc entries
  net by sign, never filtered out). revenue = ``-sum(balance)`` (AD-112: revenue positive).
- ``_cmc_gl_balance_read_group(company, date_from, date_to, groupby=("account_id", "date:month"))`` ->
  ``(*groups, balance)``, posted items only, natural sign (``sum(balance)``, debit positive).
- ``_cmc_gl_balances_at(company, date_to)`` -> ``{account.account record: balance}``: balance-sheet
  accounts (``include_initial_balance``) cumulative up to ``date_to``; P&L accounts from the start of the
  company fiscal year containing ``date_to`` (``company.compute_fiscalyear_dates``) to ``date_to``.
- Perimeter (AD-115): the readers aggregate in sudo; the caller must belong to
  ``cmc_base.group_cmc_user`` and ``company`` must be in ``env.companies``, else ``AccessError``.
  Aggregated with ``_read_group`` (AD-114): query count independent of the number of journal items.
"""

from datetime import date

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests.common import new_test_user, tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged("post_install", "-at_install")
class TestCmcReaders(AccountTestInvoicingCommon):
    """AD-111: revenue = -balance of posted income items; the GL is the truth."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.AML = cls.env["account.move.line"]
        cls.company = cls.company_data["company"]
        cls.revenue = cls.company_data["default_account_revenue"]
        cls.company_data_b = cls.setup_other_company(name="CMC readers B")
        cls.company_b = cls.company_data_b["company"]
        cls.cmc_user = new_test_user(
            cls.env, login="cmc_reader_user", groups="base.group_user,cmc_base.group_cmc_user",
            company_id=cls.company.id, company_ids=[Command.set(cls.company.ids)],
        )
        cls.outsider = new_test_user(
            cls.env, login="cmc_reader_outsider", groups="base.group_user",
            company_id=cls.company.id, company_ids=[Command.set(cls.company.ids)],
        )

    @classmethod
    def _invoice(cls, partner, amount, day, move_type="out_invoice", data=None, post=True, lines=1):
        data = data or cls.company_data
        move = cls.env["account.move"].with_company(data["company"]).create({
            "move_type": move_type,
            "partner_id": partner.id,
            "invoice_date": day,
            "date": day,
            "journal_id": data["default_journal_sale"].id,
            "invoice_line_ids": [
                Command.create({
                    "name": "line",
                    "product_id": cls.product_a.id,
                    "quantity": 1.0,
                    "price_unit": amount / lines,
                    "account_id": data["default_account_revenue"].id,
                    "tax_ids": [Command.clear()],
                })
                for _i in range(lines)
            ],
        })
        if post:
            move.action_post()
        return move

    def _revenue(self, date_from, date_to, groupby=(), company=None, env=None):
        return (env or self.env)["account.move.line"]._cmc_revenue_read_group(
            company or self.company, date_from, date_to, groupby=groupby
        )

    # ---- revenue -------------------------------------------------------------------

    def test_revenue_nets_credit_notes(self):
        # AD-111 inv.(2): credit notes net by sign, they are never filtered out
        self._invoice(self.partner_a, 1000.0, date(2026, 3, 10))
        self._invoice(self.partner_a, 300.0, date(2026, 4, 5), move_type="out_refund")
        self._invoice(self.partner_b, 500.0, date(2026, 3, 20))
        self.assertEqual(self._revenue(date(2026, 1, 1), date(2026, 12, 31)), [(1200.0,)])

    def test_revenue_by_customer_and_month(self):
        # AD-111: grouped at the fact grain the engine asks for
        self._invoice(self.partner_a, 1000.0, date(2026, 3, 10))
        self._invoice(self.partner_a, 300.0, date(2026, 4, 5), move_type="out_refund")
        self._invoice(self.partner_b, 500.0, date(2026, 3, 20))
        by_partner = dict(self._revenue(date(2026, 1, 1), date(2026, 12, 31), groupby=("partner_id",)))
        self.assertEqual(by_partner, {self.partner_a: 700.0, self.partner_b: 500.0})
        by_month = dict(self._revenue(date(2026, 1, 1), date(2026, 12, 31), groupby=("date:month",)))
        self.assertEqual(by_month, {date(2026, 3, 1): 1500.0, date(2026, 4, 1): -300.0})

    def test_only_posted_documents(self):
        # AD-111 inv.(2): drafts and cancelled documents are not revenue
        self._invoice(self.partner_a, 1000.0, date(2026, 3, 10))
        self._invoice(self.partner_a, 999.0, date(2026, 3, 11), post=False)
        cancelled = self._invoice(self.partner_a, 777.0, date(2026, 3, 12))
        cancelled.button_draft()
        cancelled.button_cancel()
        self.assertEqual(self._revenue(date(2026, 3, 1), date(2026, 3, 31)), [(1000.0,)])

    def test_span_bounds_inclusive(self):
        # AD-105: first and last day of the span are inside it
        self._invoice(self.partner_a, 100.0, date(2026, 2, 1))
        self._invoice(self.partner_a, 10.0, date(2026, 2, 28))
        self._invoice(self.partner_a, 1.0, date(2026, 3, 1))
        self.assertEqual(self._revenue(date(2026, 2, 1), date(2026, 2, 28)), [(110.0,)])

    def test_only_income_accounts(self):
        # AD-111: a vendor bill on an expense account is no revenue
        bill = self.env["account.move"].create({
            "move_type": "in_invoice", "partner_id": self.partner_a.id,
            "invoice_date": date(2026, 3, 10), "date": date(2026, 3, 10),
            "invoice_line_ids": [Command.create({
                "name": "cost", "quantity": 1.0, "price_unit": 400.0,
                "account_id": self.company_data["default_account_expense"].id, "tax_ids": [Command.clear()],
            })],
        })
        bill.action_post()
        self._invoice(self.partner_a, 1000.0, date(2026, 3, 10))
        self.assertEqual(self._revenue(date(2026, 3, 1), date(2026, 3, 31)), [(1000.0,)])

    def test_misc_entry_on_income_account_counts(self):
        # AD-111: all move types; the GL is the truth (e.g. a revenue accrual)
        entry = self.env["account.move"].create({
            "move_type": "entry", "date": date(2026, 3, 31),
            "journal_id": self.company_data["default_journal_misc"].id,
            "line_ids": [
                Command.create({"name": "accrual", "account_id": self.revenue.id, "credit": 50.0}),
                Command.create({"name": "accrual", "account_id": self.company_data["default_account_receivable"].id, "debit": 50.0}),
            ],
        })
        entry.action_post()
        self.assertEqual(self._revenue(date(2026, 3, 1), date(2026, 3, 31)), [(50.0,)])

    def test_revenue_company_isolation(self):
        # AD-115: company B revenue never leaks into company A
        self._invoice(self.partner_a, 1000.0, date(2026, 3, 10))
        self._invoice(self.partner_a, 4000.0, date(2026, 3, 10), data=self.company_data_b)
        self.assertEqual(self._revenue(date(2026, 3, 1), date(2026, 3, 31)), [(1000.0,)])
        self.assertEqual(self._revenue(date(2026, 3, 1), date(2026, 3, 31), company=self.company_b), [(4000.0,)])

    def test_cmc_user_reads_aggregates_without_accounting_rights(self):
        # AD-115: engines aggregate in sudo inside the company perimeter
        self._invoice(self.partner_a, 1000.0, date(2026, 3, 10))
        rows = self._revenue(date(2026, 3, 1), date(2026, 3, 31), env=self.env(user=self.cmc_user))
        self.assertEqual(rows, [(1000.0,)])

    def test_cmc_user_cannot_read_foreign_company(self):
        # AD-115: the sudo read never crosses the user's companies
        self._invoice(self.partner_a, 4000.0, date(2026, 3, 10), data=self.company_data_b)
        with self.assertRaises(AccessError):
            self._revenue(date(2026, 3, 1), date(2026, 3, 31), company=self.company_b, env=self.env(user=self.cmc_user))

    def test_non_cmc_user_cannot_use_reader(self):
        # AD-115: sudo aggregates are a CMC capability, not a back door to the GL
        self._invoice(self.partner_a, 1000.0, date(2026, 3, 10))
        with self.assertRaises(AccessError):
            self._revenue(date(2026, 3, 1), date(2026, 3, 31), env=self.env(user=self.outsider))

    def test_revenue_query_count_independent_of_volume(self):
        # AD-114 (1)/AD-111 inv.(5): unbounded source, aggregated, never browsed
        for _i in range(2):
            self._invoice(self.partner_a, 10.0, date(2026, 5, 10))
        for _i in range(20):
            self._invoice(self.partner_a, 10.0, date(2026, 6, 10), lines=2)

        def count(date_from, date_to):
            self.env.flush_all()
            self.env.invalidate_all()
            before = self.env.cr.sql_log_count
            rows = self._revenue(date_from, date_to, groupby=("partner_id",))
            return self.env.cr.sql_log_count - before, rows

        small, _rows = count(date(2026, 5, 1), date(2026, 5, 31))
        large, rows = count(date(2026, 6, 1), date(2026, 6, 30))
        self.assertEqual(rows, [(self.partner_a, 200.0)])
        self.assertLessEqual(large, small + 2, "40 journal items must cost the same queries as 2")

    # ---- GL balances ---------------------------------------------------------------

    def test_gl_balance_by_account_and_month(self):
        # AD-111: GL balances by account and month, natural sign (debit positive)
        self._invoice(self.partner_a, 1000.0, date(2026, 1, 15))
        self._invoice(self.partner_a, 400.0, date(2026, 2, 10))
        self._invoice(self.partner_a, 999.0, date(2026, 2, 11), post=False)
        rows = self.AML._cmc_gl_balance_read_group(self.company, date(2026, 1, 1), date(2026, 2, 28))
        income = {month: balance for account, month, balance in rows if account == self.revenue}
        self.assertEqual(income, {date(2026, 1, 1): -1000.0, date(2026, 2, 1): -400.0})

    def test_gl_balances_at_date(self):
        # AD-111: balance sheet cumulative to date, P&L within the fiscal year
        self.company.write({"fiscalyear_last_month": "12", "fiscalyear_last_day": 31})
        self._invoice(self.partner_a, 1000.0, date(2025, 12, 15))
        self._invoice(self.partner_a, 400.0, date(2026, 2, 10))
        self._invoice(self.partner_a, 50.0, date(2026, 4, 2))  # after the date
        balances = self.AML._cmc_gl_balances_at(self.company, date(2026, 3, 31))
        receivable = self.company_data["default_account_receivable"]
        self.assertAlmostEqual(balances[receivable], 1400.0, places=2)
        self.assertAlmostEqual(balances[self.revenue], -400.0, places=2)

    def test_gl_balances_company_isolation(self):
        # AD-115: GL of company B is not in company A balances
        self._invoice(self.partner_a, 4000.0, date(2026, 2, 10), data=self.company_data_b)
        balances = self.AML._cmc_gl_balances_at(self.company, date(2026, 3, 31))
        self.assertFalse(balances.get(self.revenue))
        self.assertNotIn(self.company_data_b["default_account_revenue"], balances)
