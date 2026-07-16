from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestFullCostingExtra(TransactionCase):
    """Plan-aware period pool + driver-qty guard (moved from core when full
    costing was extracted into its own module)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.AA = cls.env["account.analytic.account"]
        cls.AL = cls.env["account.analytic.line"]
        cls.driver = cls.env["cmc.cost.driver"].create({"name": "Ore", "code": "H"})
        roots = cls.env["account.analytic.plan"].search([("parent_id", "=", False)])
        cls.plan_alt = roots.filtered(lambda p: p._column_name() != "account_id")[:1]

    def _make_cost(self, center, amount, behavior="fixed"):
        col = center.plan_id._column_name()
        return self.AL.create(
            {"name": "c", col: center.id, "amount": amount, "cmc_behavior": behavior}
        )

    # period pool reads a period center on a non-default plan
    def test_period_pool_plan_aware(self):
        comm = self.AA.create(
            {"name": "Commerciale", "plan_id": self.plan_alt.id, "cmc_is_period": True}
        )
        self._make_cost(comm, 300)
        z = self.AA.create(
            {"name": "Zf", "plan_id": self.plan_alt.id, "cmc_center_type": "final"}
        )
        self._make_cost(z, 1000)
        closing = self.env["cmc.closing.run"].create({"name": "rc", "method": "direct"})
        closing.action_compute()
        p1 = self.env["product.product"].create({"name": "P1", "standard_price": 10})
        run = self.env["cmc.costing.run"].create(
            {"name": "cst", "closing_run_id": closing.id, "base": "multiple",
             "product_ids": [(6, 0, p1.ids)],
             "line_ids": [(0, 0, {"center_id": z.id, "product_id": p1.id,
                                  "driver_id": self.driver.id, "driver_qty": 10})]}
        )
        run.action_compute()
        r1 = run.result_ids.filtered(lambda r: r.product_id == p1)
        self.assertEqual(r1.cost_comm_admin, 300.0)

    # driver quantity zero is blocked
    def test_driver_qty_must_be_positive(self):
        z = self.AA.create(
            {"name": "Zd", "plan_id": self.plan_alt.id, "cmc_center_type": "final"}
        )
        self._make_cost(z, 100)
        closing = self.env["cmc.closing.run"].create({"name": "rd", "method": "direct"})
        closing.action_compute()
        p = self.env["product.product"].create({"name": "Pd", "standard_price": 5})
        with self.assertRaises(Exception):
            with self.env.cr.savepoint():
                self.env["cmc.costing.run"].create(
                    {"name": "cd", "closing_run_id": closing.id, "base": "multiple",
                     "product_ids": [(6, 0, p.ids)],
                     "line_ids": [(0, 0, {"center_id": z.id, "product_id": p.id,
                                          "driver_id": self.driver.id, "driver_qty": 0})]}
                )
                self.env.flush_all()
