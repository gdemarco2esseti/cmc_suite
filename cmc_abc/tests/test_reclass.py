from datetime import date
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestAbcReclass(TransactionCase):
    def _run(self):
        plan = self.env["account.analytic.plan"].search(
            [("parent_id", "=", False)]).filtered(
            lambda p: p._column_name() == "account_id")[:1]
        el = self.env.ref("cmc_base.cmc_element_mod")
        z = self.env["account.analytic.account"].create(
            {"name": "Z", "plan_id": plan.id, "cmc_center_type": "final"})
        act = self.env["cmc.abc.activity"].create({"name": "Set", "code": "SET"})
        self.env["account.analytic.line"].create(
            {"name": "pool", "account_id": z.id, "amount": 1000,
             "date": date(2026, 1, 15), "cmc_behavior": "variable",
             "cmc_cost_element_id": el.id, "cmc_activity_id": act.id})
        p1 = self.env["product.product"].create({"name": "P1"})
        p2 = self.env["product.product"].create({"name": "P2"})
        run = self.env["cmc.abc.run"].create(
            {"name": "ABC", "line_ids": [
                (0, 0, {"activity_id": act.id, "product_id": p1.id, "driver_qty": 30}),
                (0, 0, {"activity_id": act.id, "product_id": p2.id, "driver_qty": 10}),
            ]})
        run.action_compute()
        return run, p1, p2

    def test_structure(self):
        run, p1, p2 = self._run()
        d = run._cmc_reclass()
        labels = [r["label"] for r in d["rows"]]
        self.assertIn("Costo ABC totale", labels)
        self.assertEqual(len(d["columns"]), 2)

    def test_quadratura(self):
        run, p1, p2 = self._run()
        d = run._cmc_reclass()
        total_row = next(r for r in d["rows"] if r["label"] == "Costo ABC totale")
        act_rows = [r for r in d["rows"] if r["label"] != "Costo ABC totale"]
        for c in d["columns"]:
            k = c["key"]
            s = sum(r["values"][k] for r in act_rows)
            self.assertAlmostEqual(s, total_row["values"][k], 2)

    def test_negative_pool_sign(self):
        # B-1 regression: analytic cost lines are conventionally NEGATIVE; the
        # reclass must abs() the pool like the engine, so activity rows come out
        # POSITIVE and reconcile with the positive 'Costo ABC totale' subtotal.
        plan = self.env["account.analytic.plan"].search(
            [("parent_id", "=", False)]).filtered(
            lambda p: p._column_name() == "account_id")[:1]
        el = self.env.ref("cmc_base.cmc_element_mod")
        z = self.env["account.analytic.account"].create(
            {"name": "Zn", "plan_id": plan.id, "cmc_center_type": "final"})
        act = self.env["cmc.abc.activity"].create({"name": "Neg", "code": "NEG"})
        self.env["account.analytic.line"].create(
            {"name": "poolneg", "account_id": z.id, "amount": -1000,
             "date": __import__("datetime").date(2026, 1, 15),
             "cmc_behavior": "variable", "cmc_cost_element_id": el.id,
             "cmc_activity_id": act.id})
        p1 = self.env["product.product"].create({"name": "PN"})
        run = self.env["cmc.abc.run"].create(
            {"name": "ABCneg", "line_ids": [
                (0, 0, {"activity_id": act.id, "product_id": p1.id,
                        "driver_qty": 10})]})
        run.action_compute()
        d = run._cmc_reclass()
        total_row = next(r for r in d["rows"] if r["label"] == "Costo ABC totale")
        act_row = next(r for r in d["rows"] if r["label"] != "Costo ABC totale")
        k = "p%s" % p1.id
        self.assertGreater(act_row["values"][k], 0)          # positive, not -1000
        self.assertAlmostEqual(act_row["values"][k], total_row["values"][k], 2)
