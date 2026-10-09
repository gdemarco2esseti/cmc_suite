from datetime import date

from odoo.tests.common import TransactionCase, new_test_user, tagged
from odoo.tools import formatLang

ON_DATE = date(2026, 6, 15)
PERIOD = {"date_from": date(2026, 1, 1), "date_to": date(2026, 12, 31)}


@tagged("post_install", "-at_install")
class TestCostSign(TransactionCase):
    """AD-005 — one sign convention: a cost is a NEGATIVE `amount` on
    account.analytic.line (Odoo's natural sign: vendor bills, timesheets,
    bridges); every CMC engine reads cost = -amount, so a reversal (positive
    line) reduces the cost and manual entries never cancel document costs."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.AA = cls.env["account.analytic.account"]
        cls.AL = cls.env["account.analytic.line"]
        cls.eur = cls.env.ref("base.EUR")
        cls.eur.active = True
        # dedicated company: the cockpit KPI aggregates the whole company, demo data must not leak in
        cls.company = cls.env["res.company"].create({"name": "CMC Sign Co", "currency_id": cls.eur.id})
        cls.plan = (
            cls.env["account.analytic.plan"]
            .search([("parent_id", "=", False)])
            .filtered(lambda p: p._column_name() == "account_id")[:1]
        )
        cls.center = cls.AA.create(
            {"name": "Sign Center", "plan_id": cls.plan.id, "cmc_center_type": "final", "company_id": cls.company.id}
        )
        cls.cmc_user = new_test_user(
            cls.env,
            login="cmc_sign_user",
            groups="base.group_user,cmc_base.group_cmc_user",
            company_id=cls.company.id,
            company_ids=[(6, 0, cls.company.ids)],
        )

    def _line(self, amount, **kw):
        vals = {
            "name": "line", "account_id": self.center.id, "amount": amount,
            "company_id": self.company.id, "cmc_behavior": "fixed", "date": ON_DATE,
        }
        vals.update(kw)
        return self.AL.create(vals)

    # ---- (a) cmc_cost_amount: the user types a positive cost ---------------

    def test_cost_amount_field_is_monetary_not_stored(self):
        # AD-005: "Cost" is a presentation of -amount, never a second source of truth
        field = self.AL._fields.get("cmc_cost_amount")
        self.assertTrue(field, "account.analytic.line.cmc_cost_amount is missing")
        self.assertEqual(field.type, "monetary")
        self.assertFalse(field.store)

    def test_cost_amount_on_create_saves_negative_amount(self):
        # AD-005: a Cost Entry of 100 is stored as amount -100
        line = self.AL.create({
            "name": "manual", "account_id": self.center.id, "company_id": self.company.id,
            "cmc_behavior": "fixed", "date": ON_DATE, "cmc_cost_amount": 100.0,
        })
        self.assertEqual(line.amount, -100.0)
        self.assertEqual(line.cmc_cost_amount, 100.0)

    def test_cost_amount_on_write_saves_negative_amount(self):
        # AD-005: editing the cost to 100 rewrites amount to -100
        line = self._line(-10.0)
        line.write({"cmc_cost_amount": 100.0})
        self.assertEqual(line.amount, -100.0)

    def test_cost_amount_reads_negated_amount(self):
        # AD-005: a booked cost of amount -250 reads as cost 250
        line = self._line(-250.0)
        self.assertEqual(line.cmc_cost_amount, 250.0)

    def test_cost_amount_entered_by_cmc_user(self):
        # AD-005: the controller (group_cmc_user) enters a manual cost, not just the superuser
        line = self.AL.with_user(self.cmc_user).create({
            "name": "manual by controller", "account_id": self.center.id, "company_id": self.company.id,
            "cmc_behavior": "fixed", "date": ON_DATE, "cmc_cost_amount": 400.0,
        })
        self.assertEqual(line.amount, -400.0)

    # ---- canonical reader --------------------------------------------------

    def test_cost_read_group_returns_positive_cost(self):
        # AD-005: _cmc_cost_read_group is _read_group whose last element is cost = -sum(amount)
        lines = self._line(-1000.0) | self._line(200.0)
        res = self.AL._cmc_cost_read_group([("id", "in", lines.ids)])
        self.assertEqual(len(res), 1)
        self.assertAlmostEqual(res[0][-1], 800.0, places=2)

    def test_cost_read_group_with_groupby(self):
        # AD-005: grouped rows carry the group keys first, the positive cost last
        self._line(-300.0, cmc_behavior="fixed")
        self._line(-120.0, cmc_behavior="variable")
        res = self.AL._cmc_cost_read_group(
            [("account_id", "=", self.center.id)], groupby=["cmc_behavior"]
        )
        self.assertEqual(dict(res), {"fixed": 300.0, "variable": 120.0})

    def test_cost_read_group_empty_is_zero(self):
        # AD-005: no lines -> cost 0.0, never None
        res = self.AL._cmc_cost_read_group([("id", "=", 0)])
        self.assertEqual(res[0][-1] if res else 0.0, 0.0)

    def test_cost_groups_returns_positive_cost(self):
        # AD-005: _cmc_cost_groups returns COSTS: negative lines -> positive figure
        self._line(-1000.0)
        self.assertEqual(self.center._cmc_cost_groups(**PERIOD), {self.center.id: 1000.0})

    # ---- (b) reversal and mixed sources ------------------------------------

    def _closing_initial(self):
        run = self.env["cmc.closing.run"].create({**PERIOD, "method": "direct", "company_id": self.company.id})
        run.action_compute()
        pre = run.cost_analysis_ids.filtered(lambda l: l.center_id == self.center and l.state == "pre")
        return run, sum(pre.mapped("amount"))

    def test_reversal_reduces_center_cost_in_closing(self):
        # AD-005: cost 1000 (amount -1000) + reversal 200 (amount +200) -> center cost 800 before allocation
        self._line(-1000.0)
        self._line(200.0)
        run, pre_total = self._closing_initial()
        self.assertAlmostEqual(pre_total, 800.0, places=2)
        line = run.line_ids.filtered(lambda l: l.center_id == self.center)
        self.assertAlmostEqual(line.cost_initial, 800.0, places=2)

    def test_reversal_reduces_cockpit_booked_costs(self):
        # AD-005: the cockpit KPI shows the net cost 800, formatted in the company currency
        self._line(-1000.0)
        self._line(200.0)
        data = (
            self.env["cmc.cockpit"]
            .with_user(self.cmc_user)
            .with_context(allowed_company_ids=[self.company.id])
            .get_cockpit_data()
        )
        value = next(k["value"] for k in data["kpis"] if k["key"] == "cost")
        env = self.env(user=self.cmc_user, context={"allowed_company_ids": [self.company.id]})
        self.assertEqual(value, formatLang(env, 800.0, currency_obj=self.eur))

    def test_document_cost_and_manual_entry_add_up(self):
        # AD-005: a vendor-bill cost (-600) and a manual Cost Entry of 400 sum to 1000, they never cancel
        self._line(-600.0, name="bill")
        self.AL.create({
            "name": "manual", "account_id": self.center.id, "company_id": self.company.id,
            "cmc_behavior": "fixed", "date": ON_DATE, "cmc_cost_amount": 400.0,
        })
        self.assertEqual(self.center._cmc_cost_groups(**PERIOD), {self.center.id: 1000.0})
        _run, pre_total = self._closing_initial()
        self.assertAlmostEqual(pre_total, 1000.0, places=2)

    def test_document_cost_and_manual_entry_add_up_in_cockpit(self):
        # AD-005: same mix on the cockpit KPI: 600 + 400 = 1000 (old convention: -600 + 400 nets to 200)
        self._line(-600.0, name="bill")
        self.AL.with_user(self.cmc_user).create({
            "name": "manual", "account_id": self.center.id, "company_id": self.company.id,
            "cmc_behavior": "fixed", "date": ON_DATE, "cmc_cost_amount": 400.0,
        })
        data = (
            self.env["cmc.cockpit"]
            .with_user(self.cmc_user)
            .with_context(allowed_company_ids=[self.company.id])
            .get_cockpit_data()
        )
        value = next(k["value"] for k in data["kpis"] if k["key"] == "cost")
        env = self.env(user=self.cmc_user, context={"allowed_company_ids": [self.company.id]})
        self.assertEqual(value, formatLang(env, 1000.0, currency_obj=self.eur))
