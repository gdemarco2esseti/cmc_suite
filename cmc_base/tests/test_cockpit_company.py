from datetime import date

from odoo.tests.common import TransactionCase, new_test_user, tagged
from odoo.tools import formatLang

from odoo.addons.cmc_base.models.cmc_run_mixin import CMC_RUN_STATE


@tagged("post_install", "-at_install")
class TestCockpitCompany(TransactionCase):
    """AC2 — the cockpit formats its figures with the CURRENT company currency,
    so it must aggregate only the current company's data: with an EUR and a
    CHF company both active, CHF amounts must never be summed into EUR."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.AA = cls.env["account.analytic.account"]
        cls.AL = cls.env["account.analytic.line"]
        cls.eur = cls.env.ref("base.EUR")
        cls.chf = cls.env.ref("base.CHF")
        (cls.eur | cls.chf).write({"active": True})
        cls.company_a = cls.env["res.company"].create(
            {"name": "CMC Cockpit EUR", "currency_id": cls.eur.id}
        )
        cls.company_b = cls.env["res.company"].create(
            {"name": "CMC Cockpit CHF", "currency_id": cls.chf.id}
        )
        cls.plan = (
            cls.env["account.analytic.plan"]
            .search([("parent_id", "=", False)])
            .filtered(lambda p: p._column_name() == "account_id")[:1]
        )
        cls.user = new_test_user(
            cls.env,
            login="cmc_cockpit_mc_user",
            groups="base.group_user,cmc_base.group_cmc_user",
            company_id=cls.company_a.id,
            company_ids=[(6, 0, (cls.company_a | cls.company_b).ids)],
        )
        cls.element_a = cls.env["cmc.cost.element"].create(
            {"name": "Materials EUR", "code": "CKP_A", "company_id": cls.company_a.id}
        )
        cls.element_b = cls.env["cmc.cost.element"].create(
            {"name": "Materials CHF", "code": "CKP_B", "company_id": cls.company_b.id}
        )
        cls.center_a = cls._center("Center EUR", cls.company_a)
        cls.center_b = cls._center("Center CHF", cls.company_b)
        cls._cost(cls.center_a, cls.element_a, 100.0)
        cls._cost(cls.center_b, cls.element_b, 50.0)

    @classmethod
    def _center(cls, name, company):
        return cls.AA.create(
            {"name": name, "plan_id": cls.plan.id, "cmc_center_type": "final",
             "company_id": company.id}
        )

    @classmethod
    def _cost(cls, center, element, amount):
        # classified cost (cmc_behavior set) on a company-owned center; AD-005: cost = negative amount
        cls.AL.create(
            {"name": "cost", "account_id": center.id, "amount": -amount,
             "company_id": center.company_id.id, "cmc_behavior": "fixed",
             "cmc_cost_element_id": element.id}
        )

    def _cockpit(self, current, other):
        # allowed_company_ids[0] is the current company (env.company)
        return (
            self.env["cmc.cockpit"]
            .with_user(self.user)
            .with_context(allowed_company_ids=[current.id, other.id])
            .get_cockpit_data()
        )

    def _kpi(self, data, key):
        return next(k["value"] for k in data["kpis"] if k["key"] == key)

    def _fmt(self, amount, company):
        env = self.env(user=self.user, context={"allowed_company_ids": [company.id]})
        return formatLang(env, amount, currency_obj=company.currency_id)

    # AC2.1 — KPI "Booked Costs" = only current company (EUR 100), never 150
    def test_ac2_1_booked_costs_only_current_company(self):
        data = self._cockpit(self.company_a, self.company_b)
        value = self._kpi(data, "cost")
        self.assertNotIn("150", value, "CHF costs of company B were summed into the EUR KPI")
        self.assertEqual(value, self._fmt(100.0, self.company_a))

    # AC2.1 — symmetric: with B current the KPI is CHF 50, not 150
    def test_ac2_1_booked_costs_other_company_current(self):
        data = self._cockpit(self.company_b, self.company_a)
        value = self._kpi(data, "cost")
        self.assertNotIn("150", value, "EUR costs of company A were summed into the CHF KPI")
        self.assertEqual(value, self._fmt(50.0, self.company_b))

    # AC2.1 — cost-by-element chart carries only the current company's elements
    def test_ac2_1_chart_only_current_company(self):
        data = self._cockpit(self.company_a, self.company_b)
        self.assertEqual(data["chart"]["labels"], [self.element_a.display_name])
        self.assertEqual(data["chart"]["values"], [100.0])

    # AC2.2 — "Costed Products" counts only the current company's costing results
    def test_ac2_2_products_costed_only_current_company(self):
        if "cmc.costing.result" not in self.env:
            self.skipTest("cmc_full_costing not installed")
        product = self.env["product.product"].create({"name": "CKP product"})
        period = {"date_from": date(2026, 1, 1), "date_to": date(2026, 12, 31)}
        for company in (self.company_a, self.company_b):
            closing = self.env["cmc.closing.run"].create(
                {**period, "method": "direct", "company_id": company.id}
            )
            # a computed closing without recomputing it here
            closing.with_context(cmc_run_state=CMC_RUN_STATE).write({"state": "done"})
            run = self.env["cmc.costing.run"].create(
                {**period, "closing_run_id": closing.id, "company_id": company.id}
            )
            self.env["cmc.costing.result"].with_context(cmc_run_state=CMC_RUN_STATE).create(
                {"run_id": run.id, "product_id": product.id}
            )
        data = self._cockpit(self.company_a, self.company_b)
        self.assertEqual(self._kpi(data, "products"), "1")

    # AC2.2 — pipeline step counts follow the current company too
    def test_ac2_2_pipeline_counts_current_company(self):
        data = self._cockpit(self.company_a, self.company_b)
        costs = next(s for s in data["pipeline"] if s["key"] == "costs")
        self.assertEqual(costs["count"], 1)
