"""WP-01 / FIN-13 - COGE <-> COAN reconciliation (closed system) with an explicit unexplained difference.

CONTRACT
- Model ``cmc.coge.reconciliation`` in ``cmc_account`` (``_inherit = ["cmc.run.mixin"]``, AD-113; company rule
  ``parent_of``; ACL group_cmc_user r/w/c, manager full): ``name``, ``company_id`` (required, default
  env.company), ``date_from``, ``date_to`` (inputs), ``currency_id`` related; result header figures written
  by Compute and cleared by reset (``_cmc_reset_values``): ``coge_amount``, ``coan_amount``,
  ``figurative_amount``, ``neutral_amount``, ``unexplained_amount`` (Monetary).
- Compute (snapshot, AD-113; GL read through the cmc_account reader scope, AD-111; ``_read_group`` only):
  * COGE = sum(balance) of posted journal items of the company in the span on accounts of type ``expense``,
    ``expense_depreciation``, ``expense_direct_cost`` (credit notes net by sign);
  * COAN = cost (-amount) of the company's cost-base analytic lines (``cmc_excluded`` False) in the span,
    except lines whose ``general_account_id`` is an income account;
  * figurative = the part of COAN whose element has ``cmc_kind = 'figurative'`` (never in the GL);
  * neutral = GL expense balance in COGE whose journal item has NO analytic distribution (never reaches COAN);
  * unexplained = COGE - (COAN - figurative + neutral)   [closed system: COAN + excluded - figurative = COGE].
- A posting dated in the span AFTER compute does not change the computed figures (snapshot); reset + compute
  picks it up. Users without group_cmc_user cannot create/compute (AccessError). Other companies are ignored.

Hand-computed (span March 2031): bill 1000 on center 100% (COAN 1000), bill 200 without distribution
(neutral 200), figurative analytic line 300 -> COGE 1200, COAN 1300, figurative 300, neutral 200,
unexplained 0. + manual non-figurative analytic line 50 -> unexplained -50. + bill 400 distributed 50% ->
unexplained +200. + vendor refund 100 on center 100% -> COGE 1100, COAN 1200, unexplained 0.
"""

from datetime import date

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests.common import new_test_user, tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon

DAY = date(2031, 3, 10)
SPAN = {"date_from": date(2031, 3, 1), "date_to": date(2031, 3, 31)}


@tagged("post_install", "-at_install")
class TestCogeReconciliation(AccountTestInvoicingCommon):
    """FIN-13: management accounting reconciles to the GL; whatever does not is shown, never absorbed."""

    @classmethod
    def get_default_groups(cls):
        # why: AccountTestInvoicingCommon runs as `accountman`; the reconciliation is CMC-gated
        return super().get_default_groups() | cls.env.ref("cmc_base.group_cmc_manager")

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Rec = cls.env["cmc.coge.reconciliation"]
        cls.AL = cls.env["account.analytic.line"]
        cls.company = cls.company_data["company"]
        cls.expense = cls.company_data["default_account_expense"]
        plan = cls.env["account.analytic.plan"].search([("parent_id", "=", False)]).filtered(
            lambda p: p._column_name() == "account_id"
        )[:1]
        cls.center = cls.env["account.analytic.account"].create(
            {"name": "WP01 Rec center", "plan_id": plan.id, "cmc_center_type": "final", "company_id": cls.company.id}
        )
        cls.figurative = cls.env["cmc.cost.element"].create(
            {"name": "WP01 Imputed interest", "code": "WP01REC_FIG", "cmc_kind": "figurative"}
        )
        cls._bill(1000.0, {str(cls.center.id): 100})
        cls._bill(200.0, None)
        cls._analytic(300.0, element=cls.figurative)
        cls.company_data_b = cls.setup_other_company(name="WP01 Rec B")
        cls.cmc_user = new_test_user(
            cls.env, login="wp01_rec_user", groups="base.group_user,cmc_base.group_cmc_user",
            company_id=cls.company.id, company_ids=[Command.set(cls.company.ids)],
        )
        cls.outsider = new_test_user(
            cls.env, login="wp01_rec_out", groups="base.group_user",
            company_id=cls.company.id, company_ids=[Command.set(cls.company.ids)],
        )

    @classmethod
    def _bill(cls, amount, distribution, move_type="in_invoice", data=None, day=DAY):
        data = data or cls.company_data
        line = {"name": "service", "quantity": 1.0, "price_unit": amount,
                "account_id": data["default_account_expense"].id, "tax_ids": [Command.clear()]}
        if distribution:
            line["analytic_distribution"] = distribution
        move = cls.env["account.move"].with_company(data["company"]).create({
            "move_type": move_type, "partner_id": cls.partner_a.id, "invoice_date": day, "date": day,
            "journal_id": data["default_journal_purchase"].id, "invoice_line_ids": [Command.create(line)],
        })
        move.action_post()
        return move

    @classmethod
    def _analytic(cls, cost, element=None):
        return cls.AL.with_company(cls.company).create({
            "name": "manual", "account_id": cls.center.id, "amount": -cost, "date": DAY,
            "company_id": cls.company.id, "cmc_cost_element_id": element.id if element else False,
        })

    def _compute(self, env=None):
        rec = (env or self.env)["cmc.coge.reconciliation"].create({**SPAN, "name": "WP01", "company_id": self.company.id})
        rec.action_compute()
        return rec

    def test_closed_system_balances(self):
        # FIN-13: COAN + neutral - figurative = COGE
        rec = self._compute()
        self.assertRecordValues(rec, [{
            "state": "done", "coge_amount": 1200.0, "coan_amount": 1300.0, "figurative_amount": 300.0,
            "neutral_amount": 200.0, "unexplained_amount": 0.0,
        }])

    def test_analytic_without_gl_is_unexplained(self):
        # FIN-13: a non-figurative analytic cost with no GL counterpart is shown, not absorbed
        self._analytic(50.0)
        rec = self._compute()
        self.assertAlmostEqual(rec.unexplained_amount, -50.0)

    def test_partial_distribution_is_unexplained(self):
        # FIN-13: a GL cost carried to COAN only in part leaves a difference
        self._bill(400.0, {str(self.center.id): 50})
        rec = self._compute()
        self.assertAlmostEqual(rec.coge_amount, 1600.0)
        self.assertAlmostEqual(rec.coan_amount, 1500.0)
        self.assertAlmostEqual(rec.unexplained_amount, 200.0)

    def test_vendor_refund_nets_on_both_sides(self):
        # AD-111 inv.(2): credit notes net by sign in COGE and COAN
        self._bill(100.0, {str(self.center.id): 100}, move_type="in_refund")
        rec = self._compute()
        self.assertAlmostEqual(rec.coge_amount, 1100.0)
        self.assertAlmostEqual(rec.coan_amount, 1200.0)
        self.assertAlmostEqual(rec.unexplained_amount, 0.0)

    def test_snapshot_ignores_late_postings(self):
        # AD-111 inv.(3) / AD-113: back-dated postings show up only after reset + compute
        rec = self._compute()
        self._bill(500.0, None)
        self.assertAlmostEqual(rec.coge_amount, 1200.0)
        rec.action_reset_to_draft()
        self.assertFalse(rec.coge_amount)
        rec.action_compute()
        self.assertAlmostEqual(rec.coge_amount, 1700.0)
        self.assertAlmostEqual(rec.neutral_amount, 700.0)

    def test_other_company_ignored(self):
        # AD-115: the reconciliation is per company
        self._bill(999.0, None, data=self.company_data_b)
        rec = self._compute()
        self.assertAlmostEqual(rec.coge_amount, 1200.0)

    def test_cmc_user_computes(self):
        # AD-115: a Controlling User without accounting rights reconciles his company
        rec = self._compute(env=self.env(user=self.cmc_user))
        self.assertAlmostEqual(rec.unexplained_amount, 0.0)

    def test_outsider_refused(self):
        # AD-115: no CMC group, no reconciliation
        with self.assertRaises(AccessError):
            self._compute(env=self.env(user=self.outsider))

    def test_query_count_independent_of_postings(self):
        # AD-114: grouped reads; more journal items do not add queries
        def count():
            rec = self.Rec.create({**SPAN, "name": "count", "company_id": self.company.id})
            self.env.invalidate_all()
            before = self.cr.sql_log_count
            rec.action_compute()
            return self.cr.sql_log_count - before

        small = count()
        for _i in range(15):
            self._bill(10.0, {str(self.center.id): 100})
            self._bill(10.0, None)
        large = count()
        self.assertLessEqual(large, small + 2, f"query count grows with the postings: {small} -> {large}")
