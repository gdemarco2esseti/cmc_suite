from datetime import date

from odoo.tests.common import TransactionCase, tagged

TOTAL_ROW = "Total ABC cost"


@tagged("post_install", "-at_install")
class TestAbcReclass(TransactionCase):
    def _plan(self):
        return self.env["account.analytic.plan"].search(
            [("parent_id", "=", False)]).filtered(
            lambda p: p._column_name() == "account_id")[:1]

    def _run(self):
        el = self.env.ref("cmc_base.cmc_element_mod")
        z = self.env["account.analytic.account"].create(
            {"name": "Z", "plan_id": self._plan().id, "cmc_center_type": "final"})
        act = self.env["cmc.abc.activity"].create({"name": "Set", "code": "SET"})
        self.env["account.analytic.line"].create(
            {"name": "pool", "account_id": z.id, "amount": -1000,
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
        return run, p1, p2, z, act, el

    def _assert_squares(self, d):
        total_row = next(r for r in d["rows"] if r["label"] == TOTAL_ROW)
        act_rows = [r for r in d["rows"] if r["label"] != TOTAL_ROW]
        for c in d["columns"]:
            k = c["key"]
            s = sum(r["values"][k] for r in act_rows)
            self.assertAlmostEqual(s, total_row["values"][k], 2)

    def test_structure(self):
        run, p1, p2 = self._run()[:3]
        d = run._cmc_reclass()
        labels = [r["label"] for r in d["rows"]]
        self.assertIn(TOTAL_ROW, labels)
        self.assertEqual(len(d["columns"]), 2)

    def test_quadratura(self):
        run = self._run()[0]
        self._assert_squares(run._cmc_reclass())

    def test_negative_pool_sign(self):
        # B-1 regression / AD-005: analytic cost lines are NEGATIVE (cost =
        # -amount); the engine turns the pool into a positive cost, so activity
        # rows come out POSITIVE and reconcile with the positive total subtotal.
        el = self.env.ref("cmc_base.cmc_element_mod")
        z = self.env["account.analytic.account"].create(
            {"name": "Zn", "plan_id": self._plan().id, "cmc_center_type": "final"})
        act = self.env["cmc.abc.activity"].create({"name": "Neg", "code": "NEG"})
        self.env["account.analytic.line"].create(
            {"name": "poolneg", "account_id": z.id, "amount": -1000,
             "date": date(2026, 1, 15),
             "cmc_behavior": "variable", "cmc_cost_element_id": el.id,
             "cmc_activity_id": act.id})
        p1 = self.env["product.product"].create({"name": "PN"})
        run = self.env["cmc.abc.run"].create(
            {"name": "ABCneg", "line_ids": [
                (0, 0, {"activity_id": act.id, "product_id": p1.id,
                        "driver_qty": 10})]})
        run.action_compute()
        d = run._cmc_reclass()
        total_row = next(r for r in d["rows"] if r["label"] == TOTAL_ROW)
        act_row = next(r for r in d["rows"] if r["label"] != TOTAL_ROW)
        k = "p%s" % p1.id
        self.assertGreater(act_row["values"][k], 0)          # positive, not -1000
        self.assertAlmostEqual(act_row["values"][k], total_row["values"][k], 2)

    # --- B-1: the reclass must read frozen data only ------------------------
    def test_draft_run_has_no_reclass(self):
        act = self.env["cmc.abc.activity"].create({"name": "D", "code": "DR"})
        p1 = self.env["product.product"].create({"name": "PD"})
        run = self.env["cmc.abc.run"].create(
            {"name": "draft", "line_ids": [
                (0, 0, {"activity_id": act.id, "product_id": p1.id,
                        "driver_qty": 10})]})
        self.assertEqual(run.state, "draft")
        self.assertFalse(run._cmc_reclass())
        self.assertFalse(run.cmc_reclass_json)

    def test_reclass_ignores_lines_tagged_after_the_run(self):
        run, p1, p2, z, act, el = self._run()
        before = run._cmc_reclass()
        # more cost tagged to the same activity AFTER the run was computed
        self.env["account.analytic.line"].create(
            {"name": "late", "account_id": z.id, "amount": -9999,
             "date": date(2026, 1, 20), "cmc_behavior": "variable",
             "cmc_cost_element_id": el.id, "cmc_activity_id": act.id})
        run.invalidate_recordset()
        after = run._cmc_reclass()
        self.assertEqual(before, after)
        self._assert_squares(after)
