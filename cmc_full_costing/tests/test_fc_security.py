from datetime import date

from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestFullCostingSecurity(TransactionCase):
    """Every other test in this module runs as superuser, so the ACL and
    record-rule surface is invisible to them. These run as a real CMC user."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.AA = cls.env["account.analytic.account"]
        cls.AL = cls.env["account.analytic.line"]
        cls.plan = (
            cls.env["account.analytic.plan"]
            .search([("parent_id", "=", False)])
            .filtered(lambda p: p._column_name() == "account_id")[:1]
        )
        cls.company_a = cls.env["res.company"].create({"name": "CMC FC A"})
        cls.company_b = cls.env["res.company"].create({"name": "CMC FC B"})
        cls.user_a = cls._cmc_user("cmc_fc_user_a", cls.company_a)
        cls.user_b = cls._cmc_user("cmc_fc_user_b", cls.company_b)

    @classmethod
    def _cmc_user(cls, login, company):
        # v19: the m2m is `group_ids` (`groups_id` was renamed)
        return cls.env["res.users"].create(
            {
                "name": login,
                "login": login,
                "company_id": company.id,
                "company_ids": [(6, 0, company.ids)],
                "group_ids": [
                    (
                        6,
                        0,
                        [
                            cls.env.ref("base.group_user").id,
                            cls.env.ref("cmc_base.group_cmc_user").id,
                        ],
                    )
                ],
            }
        )

    @classmethod
    def _fixture(cls, company, pool=900.0, qty=90.0):
        """A complete, self-contained costing run inside `company`."""
        env = cls.env(context=dict(cls.env.context, allowed_company_ids=company.ids))
        center = env["account.analytic.account"].create(
            {"name": "FC center %s" % company.name, "plan_id": cls.plan.id,
             "cmc_center_type": "final", "company_id": company.id}
        )
        env["account.analytic.line"].create(
            {"name": "ind", "account_id": center.id, "amount": pool,
             "company_id": company.id, "cmc_behavior": "fixed",
             "cmc_directness": "indirect"}
        )
        driver = env["cmc.cost.driver"].create(
            {"name": "H %s" % company.name, "code": "H", "company_id": company.id}
        )
        product = env["product.product"].create(
            {"name": "FC product %s" % company.name, "standard_price": 10}
        )
        closing = env["cmc.closing.run"].create(
            {"name": "cl", "method": "direct", "company_id": company.id}
        )
        closing.action_compute()
        run = env["cmc.costing.run"].create(
            {
                "name": "run %s" % company.name,
                "company_id": company.id,
                "closing_run_id": closing.id,
                "date_from": date(date.today().year, 1, 1),
                "date_to": date(date.today().year, 12, 31),
                "base": "multiple",
                "product_ids": [(6, 0, product.ids)],
                "line_ids": [
                    (0, 0, {"center_id": center.id, "product_id": product.id,
                            "driver_id": driver.id, "driver_qty": qty})
                ],
            }
        )
        run.action_compute()
        return run, center, driver, product, closing

    # ---- the engine runs end to end for a plain CMC user ------------------

    def test_engine_end_to_end_as_cmc_user(self):
        _run, center, driver, product, closing = self._fixture(self.company_a)
        run = (
            self.env["cmc.costing.run"]
            .with_user(self.user_a)
            .with_company(self.company_a)
            .create(
                {
                    "name": "user run",
                    "closing_run_id": closing.id,
                    "date_from": date(date.today().year, 1, 1),
                    "date_to": date(date.today().year, 12, 31),
                    "base": "multiple",
                    "product_ids": [(6, 0, product.ids)],
                    "line_ids": [
                        (0, 0, {"center_id": center.id, "product_id": product.id,
                                "driver_id": driver.id, "driver_qty": 90})
                    ],
                }
            )
        )
        run.action_compute()
        self.assertEqual(run.state, "done")
        self.assertEqual(sum(run.result_ids.mapped("cost_indiretto")), 900.0)
        self.assertTrue(run.result_ids.quota_ids)

    def test_cmc_user_cannot_inject_derived_rows(self):
        """result/quota/config are engine-written: a user may read them, never
        forge them over RPC into a run they can merely read."""
        run, _c, _d, product, _cl = self._fixture(self.company_a)
        with self.assertRaises(AccessError):
            self.env["cmc.costing.result"].with_user(self.user_a).create(
                {"run_id": run.id, "product_id": product.id, "cost_primo": 1.0}
            )

    # ---- multi-company isolation (P0) ------------------------------------

    def test_quota_is_company_isolated(self):
        """cmc.costing.quota carries the indirect amounts of every product.
        Without a company_id + ir.rule a plain search_read returned them for
        EVERY company."""
        run_a, _c, _d, _p, _cl = self._fixture(self.company_a)
        quotas_a = run_a.result_ids.quota_ids
        self.assertTrue(quotas_a)
        self.assertEqual(quotas_a.mapped("company_id"), self.company_a)

        Quota = self.env["cmc.costing.quota"]
        visible_to_a = Quota.with_user(self.user_a).search([("id", "in", quotas_a.ids)])
        self.assertEqual(visible_to_a, quotas_a)
        visible_to_b = Quota.with_user(self.user_b).search([("id", "in", quotas_a.ids)])
        self.assertFalse(
            visible_to_b, "company B must not see company A indirect quotas"
        )
        with self.assertRaises(AccessError):
            quotas_a.with_user(self.user_b).read(["amount"])

    def test_config_is_company_isolated(self):
        run_a, _c, _d, _p, _cl = self._fixture(self.company_a)
        configs_a = run_a.result_ids.config_ids
        self.assertTrue(configs_a)
        Config = self.env["cmc.costing.config"]
        self.assertFalse(
            Config.with_user(self.user_b).search([("id", "in", configs_a.ids)])
        )

    def test_two_company_runs_do_not_mix(self):
        run_a, _ca, _da, _pa, _cla = self._fixture(self.company_a, pool=900.0)
        run_b, _cb, _db, _pb, _clb = self._fixture(self.company_b, pool=400.0)
        self.assertEqual(sum(run_a.result_ids.mapped("cost_indiretto")), 900.0)
        self.assertEqual(sum(run_b.result_ids.mapped("cost_indiretto")), 400.0)
        Run = self.env["cmc.costing.run"]
        self.assertFalse(Run.with_user(self.user_b).search([("id", "=", run_a.id)]))
