from datetime import date
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestFullReclass(TransactionCase):
    def _run(self):
        plan = self.env["account.analytic.plan"].search(
            [("parent_id", "=", False)]).filtered(
            lambda p: p._column_name() == "account_id")[:1]
        el = self.env.ref("cmc_base.cmc_element_mod")
        z = self.env["account.analytic.account"].create(
            {"name": "Z", "plan_id": plan.id, "cmc_center_type": "final"})
        self.env["account.analytic.line"].create(
            {"name": "c", "account_id": z.id, "amount": -3000, "date": date(2026, 1, 15),
             "cmc_behavior": "fixed", "cmc_cost_element_id": el.id})
        drv = self.env["cmc.cost.driver"].create({"name": "H", "code": "H"})
        p1 = self.env["product.product"].create({"name": "P1"})
        closing = self.env["cmc.closing.run"].create(
            {"name": "cl", "method": "direct",
             "date_from": date(2026, 1, 1), "date_to": date(2026, 12, 31)})
        closing.action_compute()
        run = self.env["cmc.costing.run"].create(
            {"name": "FC", "closing_run_id": closing.id, "base": "multiple",
             "date_from": date(2026, 1, 1), "date_to": date(2026, 12, 31),
             "product_ids": [(6, 0, p1.ids)],
             "line_ids": [(0, 0, {"center_id": z.id, "product_id": p1.id,
                                  "driver_id": drv.id, "driver_qty": 10})]})
        run.action_compute()
        return run

    def test_structure(self):
        d = self._run()._cmc_reclass()
        labels = [r["label"] for r in d["rows"]]
        self.assertIn("Industrial cost", labels)
        self.assertIn("Economic-technical cost", labels)
        self.assertTrue(d["columns"])

    def test_quadratura(self):
        run = self._run()
        d = run._cmc_reclass()
        rows = {r["label"]: r for r in d["rows"]}
        for c in d["columns"]:
            k = c["key"]
            self.assertAlmostEqual(
                rows["Industrial cost"]["values"][k],
                rows["Prime cost"]["values"][k]
                + rows["Industrial overhead"]["values"][k], 2)
            self.assertAlmostEqual(
                rows["Economic-technical cost"]["values"][k],
                rows["Total cost"]["values"][k]
                + rows["Figurative"]["values"][k], 2)
