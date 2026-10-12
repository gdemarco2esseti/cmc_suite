"""Stress test 2026-10 (corsia A) - FX-017: the manual classification selections are translated."""

from datetime import date

from odoo import Command
from odoo.fields import Domain
from odoo.tests.common import TransactionCase, tagged
from odoo.tools import formatLang
from odoo.tools.safe_eval import safe_eval

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged("post_install", "-at_install")
class TestFx017ManualClassificationLabels(TransactionCase):
    """FX-017: selections borrowed from account.analytic.line show the user's language, like the source field."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"]._activate_lang("it_IT")

    def _labels(self, model, fname, lang):
        return dict(self.env[model].with_context(lang=lang).fields_get([fname])[fname]["selection"])

    def test_fx017_reference_translation_present(self):
        # guard: the source fields ARE translated, otherwise the tests below prove nothing
        for fname, key in (("cmc_behavior", "fixed"), ("cmc_directness", "direct")):
            it = self._labels("account.analytic.line", fname, "it_IT")
            en = self._labels("account.analytic.line", fname, "en_US")
            self.assertNotEqual(it[key], en[key], fname)

    def test_fx017_manual_classification_behavior_translated(self):
        # FX-017 AC: no English label for an Italian user
        expected = self._labels("account.analytic.line", "cmc_behavior", "it_IT")
        self.assertEqual(self._labels("cmc.manual.classification", "cmc_behavior", "it_IT"), expected)

    def test_fx017_manual_classification_directness_translated(self):
        # FX-017 AC: no English label for an Italian user
        expected = self._labels("account.analytic.line", "cmc_directness", "it_IT")
        self.assertEqual(self._labels("cmc.manual.classification", "cmc_directness", "it_IT"), expected)


# ---------------------------------------------------------------------------------------------------------------
# RN-004 (ronin) - the cost base with the accounting bridge installed
# ---------------------------------------------------------------------------------------------------------------
# CONTRACT (binding for the developer)
# With cmc_account installed the cost base is
#     not cmc_excluded AND (cmc_behavior set
#                           OR (general_account_id set AND general_account_id.account_type NOT IN REVENUE_TYPES)
#                           OR (general_account_id not set AND amount < 0))
# (REVENUE_TYPES = cmc_account.models.account_move_line.REVENUE_TYPES, re-exported by cmc_coge_reconciliation).
# It applies to BOTH:
#   - ``account.analytic.line._cmc_cost_base_domain()`` (hence the cockpit "Booked Costs" KPI and chart);
#   - the domain of ``cmc_base.action_cmc_analytic_lines`` (Cost Entries) as stored in the DB.
# Today the base is "behaviour set OR amount < 0": a customer credit note (income account, amount < 0) is
# counted as a cost, a vendor refund (expense account, amount > 0, unclassified) is dropped from the costs.

DAY_RN004 = date(2031, 5, 12)


@tagged("post_install", "-at_install")
class TestRn004CostBaseRevenueSign(AccountTestInvoicingCommon):
    """RN-004: the cost base follows the GL account type, not the analytic sign, when the line has one."""

    @classmethod
    def get_default_groups(cls):
        # why: AccountTestInvoicingCommon runs as `accountman`; the cockpit KPI needs CMC read access
        return super().get_default_groups() | cls.env.ref("cmc_base.group_cmc_manager")

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.AL = cls.env["account.analytic.line"]
        cls.company = cls.company_data["company"]
        # why: no classification rule may give a behaviour to the fixture lines, or the sign rule is never reached
        cls.env["cmc.classification.rule"].sudo().with_context(active_test=False).search([]).write({"active": False})
        plan = cls.env["account.analytic.plan"].search([("parent_id", "=", False)]).filtered(
            lambda p: p._column_name() == "account_id"
        )[:1]
        cls.center = cls.env["account.analytic.account"].create(
            {"name": "RN004 center", "plan_id": plan.id, "cmc_center_type": "final", "company_id": cls.company.id}
        )
        dist = {str(cls.center.id): 100}
        revenue = cls.company_data["default_account_revenue"]
        expense = cls.company_data["default_account_expense"]
        sale = cls.company_data["default_journal_sale"]
        purchase = cls.company_data["default_journal_purchase"]
        cls.inv_line = cls._post("out_invoice", 1000.0, revenue, sale, dist)
        cls.out_refund_line = cls._post("out_refund", 300.0, revenue, sale, dist)
        cls.bill_line = cls._post("in_invoice", 500.0, expense, purchase, dist)
        cls.in_refund_line = cls._post("in_refund", 80.0, expense, purchase, dist)
        cls.manual_line = cls.AL.with_company(cls.company).create({
            "name": "RN004 manual cost", "account_id": cls.center.id, "amount": -100.0, "date": DAY_RN004,
            "company_id": cls.company.id,
        })
        cls.fixture = cls.inv_line | cls.out_refund_line | cls.bill_line | cls.in_refund_line | cls.manual_line
        # cost base: bill 500 - vendor refund 80 + manual 100
        cls.expected_in = cls.bill_line | cls.in_refund_line | cls.manual_line
        cls.expected_cost = 520.0

    @classmethod
    def _post(cls, move_type, price, account, journal, distribution):
        move = cls.env["account.move"].with_company(cls.company).create({
            "move_type": move_type, "partner_id": cls.partner_a.id, "invoice_date": DAY_RN004, "date": DAY_RN004,
            "journal_id": journal.id,
            "invoice_line_ids": [Command.create({
                "name": "RN004 %s" % move_type, "quantity": 1.0, "price_unit": price, "account_id": account.id,
                "tax_ids": [Command.clear()], "analytic_distribution": distribution,
            })],
        })
        move.action_post()
        line = move.line_ids.analytic_line_ids
        assert len(line) == 1, "fixture: %s must produce one analytic line" % move_type
        return line

    def _in_cost_base(self):
        domain = Domain(self.AL._cmc_cost_base_domain()) & Domain("id", "in", self.fixture.ids)
        return self.AL.search(domain)

    def test_rn004_fixture_signs_and_no_behaviour(self):
        # guard: the fixture really has the signs of the contract and no behaviour, otherwise the tests prove nothing
        self.assertRecordValues(self.fixture, [
            {"amount": 1000.0, "cmc_behavior": False, "cmc_excluded": False},
            {"amount": -300.0, "cmc_behavior": False, "cmc_excluded": False},
            {"amount": -500.0, "cmc_behavior": False, "cmc_excluded": False},
            {"amount": 80.0, "cmc_behavior": False, "cmc_excluded": False},
            {"amount": -100.0, "cmc_behavior": False, "cmc_excluded": False},
        ])
        self.assertEqual(self.inv_line.general_account_id.account_type, "income")
        self.assertEqual(self.in_refund_line.general_account_id.account_type, "expense")
        self.assertFalse(self.manual_line.general_account_id)

    def test_rn004_customer_invoice_not_a_cost(self):
        # RN-004: income account, positive -> revenue, out of the cost base
        self.assertNotIn(self.inv_line, self._in_cost_base())

    def test_rn004_customer_credit_note_not_a_cost(self):
        # RN-004: a credit note on an income account is a revenue reduction, not a cost, despite amount < 0
        self.assertNotIn(self.out_refund_line, self._in_cost_base())

    def test_rn004_vendor_bill_is_a_cost(self):
        # RN-004: expense account, negative -> cost
        self.assertIn(self.bill_line, self._in_cost_base())

    def test_rn004_vendor_refund_nets_the_cost(self):
        # RN-004: a vendor refund on an expense account stays in the cost base (it reduces the cost), even unclassified
        self.assertIn(self.in_refund_line, self._in_cost_base())

    def test_rn004_manual_entry_without_gl_is_a_cost(self):
        # RN-004: no GL account -> the sign decides: amount < 0 is a cost
        self.assertIn(self.manual_line, self._in_cost_base())

    def test_rn004_cost_base_exact_set(self):
        # RN-004: the whole cost base of the fixture, in one assertion
        self.assertEqual(self._in_cost_base(), self.expected_in)

    def test_rn004_excluded_line_out_of_cost_base(self):
        # RN-004: cmc_excluded still wins over every other condition
        self.bill_line.sudo().cmc_excluded = True
        self.assertNotIn(self.bill_line, self._in_cost_base())

    def test_rn004_cockpit_booked_costs(self):
        # RN-004: the cockpit "Booked Costs" = 500 - 80 + 100, not 300 + 500 + 100
        data = self.env["cmc.cockpit"].with_company(self.company).get_cockpit_data()
        kpi = next(k for k in data["kpis"] if k["key"] == "cost")
        expected = formatLang(self.env, self.expected_cost, currency_obj=self.company.currency_id)
        self.assertEqual(kpi["value"], expected)

    def test_rn004_cost_entries_action_domain(self):
        # RN-004: the Cost Entries menu lists the same lines the cockpit counts
        action = self.env.ref("cmc_base.action_cmc_analytic_lines")
        domain = safe_eval(action.domain or "[]", {"uid": self.env.uid, "context": {}, "allowed_company_ids": self.company.ids})
        found = self.AL.search(Domain(domain) & Domain("id", "in", self.fixture.ids))
        self.assertEqual(found, self.expected_in)
