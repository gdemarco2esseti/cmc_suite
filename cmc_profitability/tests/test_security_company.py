"""WP-09 AD-115 - security and multi-company of ``cmc_profitability``; actual unit costs in a second company.

CONTRACT
- ACL: ``cmc_base.group_cmc_user`` creates / computes / resets runs and reads every result model;
  results are read-only for everybody (written under the run sentinel only: UserError / AccessError);
  ``cmc.profit.price.element`` is configured by ``group_cmc_manager`` only (user read); non-CMC users: no access.
- Every model has ``company_id`` (results: related stored) and a global ``ir.rule``
  ``company_id parent_of company_ids`` that is NOT noupdate; a user of company B sees nothing of company A.
- Revenue and GL are read in sudo inside the run company only (cmc_account readers); a run of company B
  never shows company A items.
- Actual unit costs: with no ``cost_version_id`` the unit costs are the most recent rates published by a
  computed run (``cmc.rate._cmc_lookup`` without origin) - here the ABC run of company B (output measure).
"""

from datetime import date

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests.common import tagged

from .common import D_FROM, D_TO, ProfitCase

RESULT_MODELS = (
    "cmc.profit.fact", "cmc.profit.customer", "cmc.profit.product", "cmc.profit.channel",
    "cmc.profit.waterfall", "cmc.profit.supplier", "cmc.profit.sbu",
)


@tagged("post_install", "-at_install")
class TestSecurityCompany(ProfitCase):
    def test_cmc_user_computes_and_reads(self):
        # AD-115: the controlling user runs the profitability analysis end to end
        run = self._run(compute=False, env=self.env(user=self.cmc_user))
        run.action_compute()
        self.assertEqual(run.state, "done")
        self.assertEqual(len(run.with_user(self.cmc_user).fact_ids), 5)
        self.assertAlmostEqual(sum(run.with_user(self.cmc_user).customer_ids.mapped("customer_profit")), 600.0)
        run.action_reset_to_draft()
        self.assertFalse(run.fact_ids)

    def test_results_read_only(self):
        # AD-113 / AD-115: a result row is written by Compute only, even for its run's user
        run = self._run()
        fact = run.fact_ids.with_user(self.cmc_user)[:1]
        with self.assertRaises(UserError):
            fact.revenue = 1.0
        with self.assertRaises(UserError):
            fact.unlink()
        with self.assertRaises(UserError):
            self.env["cmc.profit.fact"].with_user(self.cmc_user).create({"run_id": run.id, "revenue": 1.0})

    def test_outsider_has_no_access(self):
        # AD-115: no CMC group, no profitability figures
        run = self._run()
        with self.assertRaises(AccessError):
            run.with_user(self.outsider).read(["operating_profit"])
        for model in RESULT_MODELS:
            with self.subTest(model=model), self.assertRaises(AccessError):
                self.env[model].with_user(self.outsider).search([])

    def test_price_elements_configured_by_manager(self):
        # AD-115: configuration belongs to the manager; the user reads it
        self._price_elements()
        Element = self.env["cmc.profit.price.element"].with_user(self.cmc_user)
        self.assertEqual(len(Element.search([("company_id", "=", self.company.id)])), 2)
        with self.assertRaises(AccessError):
            Element.create({"name": "User element", "code": "USR", "company_id": self.company.id,
                            "account_ids": [Command.set(self.freight_account.ids)]})

    def test_company_isolation(self):
        # AD-115: a CMC user of company B neither sees nor computes the runs of company A
        run = self._run()
        as_b = self.env(user=self.cmc_user_b, context={"allowed_company_ids": self.company_b.ids})
        self.assertFalse(as_b["cmc.profit.run"].search([("id", "=", run.id)]))
        self.assertFalse(as_b["cmc.profit.customer"].search([("run_id", "=", run.id)]))
        with self.assertRaises(AccessError):
            run.with_env(as_b).read(["operating_profit"])

    def test_record_rules_not_noupdate(self):
        # AD-115: company rules exist on the run and on every result model and are updatable
        for model in ("cmc.profit.run", "cmc.profit.price.element", "cmc.transfer.charge", *RESULT_MODELS):
            with self.subTest(model=model):
                rules = self.env["ir.rule"].search([("model_id.model", "=", model), ("global", "=", True)])
                self.assertTrue(rules.filtered(lambda r: "parent_of" in (r.domain_force or "")))
                xmlids = self.env["ir.model.data"].search([("model", "=", "ir.rule"), ("res_id", "in", rules.ids)])
                self.assertTrue(xmlids)
                self.assertFalse(any(xmlids.mapped("noupdate")))

    def test_company_b_run_with_actual_unit_costs(self):
        # AD-111 / AD-108: company B reads only its own GL; without a version the unit cost is the actual
        # rate published by the ABC run of company B (120 variable / 12 units = 10)
        company = self.company_b
        env_b = self.env(context=dict(self.env.context, allowed_company_ids=company.ids))
        partner = env_b["res.partner"].create({"name": "WP09 CB", "is_company": True})
        self._invoice(partner, [(self.p1, 4, 100.0, 0.0)], date(2027, 3, 10), data=self.company_data_b)
        element = env_b["cmc.cost.element"].create({"name": "WP09 B sal", "code": "WP09BSAL", "company_id": company.id})
        center = env_b["account.analytic.account"].create({"name": "WP09 B Z", "plan_id": self.plan.id, "cmc_center_type": "final",
                                                           "company_id": company.id})
        env_b["account.analytic.line"].create({"name": "wp09 b", "account_id": center.id, "amount": -120.0, "company_id": company.id,
                                               "cmc_cost_element_id": element.id, "cmc_behavior": "variable", "date": date(2027, 3, 15)})
        hours = self._driver("WP09BH", "duration", level="unit", company=company)
        units = self._driver("WP09BU", "transaction", kind="statistical", company=company)
        obj = env_b["cmc.cost.object"]._cmc_ensure_objects(self.p1, company=company)
        env_b["cmc.measure.quantity"].create({"driver_id": units.id, "date": date(2027, 3, 15), "company_id": company.id,
                                              "cost_object_id": obj.id, "quantity": 12.0})
        Act = env_b["cmc.abc.activity"]
        process = Act.create({"name": "WP09 B make", "code": "WP09BP", "node_type": "process", "company_id": company.id})
        mach = Act.create({"name": "WP09 B machines", "code": "WP09BM", "node_type": "activity", "parent_id": process.id,
                           "cost_level": "unit", "driver_id": hours.id, "company_id": company.id})
        resource = env_b["cmc.abc.resource"].create({"name": "WP09 B res", "code": "WP09BR", "center_id": center.id, "company_id": company.id})
        abc = env_b["cmc.abc.run"].create({
            "name": "WP09 B ABC", "company_id": company.id, "date_from": D_FROM, "date_to": D_TO, "output_measure_id": units.id,
            "resource_line_ids": [Command.create({"resource_id": resource.id, "activity_id": mach.id, "percent": 100})],
            "line_ids": [Command.create({"activity_id": mach.id, "cost_object_id": obj.id, "driver_id": hours.id, "driver_qty": 6})],
        })
        abc.action_compute()
        run = env_b["cmc.profit.run"].with_user(self.cmc_user_b).create({
            "name": "WP09 B March", "company_id": company.id, "date_from": D_FROM, "date_to": D_TO,
            "unit_cost_driver_id": units.id,
        })
        run.action_compute()
        self.assertRecordValues(run.fact_ids, [{
            "product_id": self.p1.id, "partner_id": partner.id, "quantity": 4.0, "revenue": 400.0,
            "variable_unit_cost": 10.0, "variable_cost": -40.0, "contribution_margin": 360.0,
        }])
        self.assertAlmostEqual(run.revenue_total, 400.0)
