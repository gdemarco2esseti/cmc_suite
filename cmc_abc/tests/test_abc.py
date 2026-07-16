from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestAbc(TransactionCase):
    """Fase 3 — Activity-Based Costing: two-stage allocation."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.AA = cls.env["account.analytic.account"]
        cls.AL = cls.env["account.analytic.line"]
        cls.Act = cls.env["cmc.abc.activity"]
        cls.Run = cls.env["cmc.abc.run"]
        cls.plan = cls.env["account.analytic.plan"].search(
            [("parent_id", "=", False)]
        ).filtered(lambda p: p._column_name() == "account_id")[:1]
        cls.center = cls.AA.create(
            {"name": "Reparto", "plan_id": cls.plan.id, "cmc_center_type": "final"}
        )
        cls.a = cls.Act.create({"name": "Setup", "code": "SET"})
        cls.b = cls.Act.create({"name": "Collaudo", "code": "QC"})
        cls.p1 = cls.env["product.product"].create({"name": "P1"})
        cls.p2 = cls.env["product.product"].create({"name": "P2"})

    @classmethod
    def _pool(cls, activity, amount):
        cls.AL.create({
            "name": "cost", "account_id": cls.center.id,
            "amount": amount, "cmc_activity_id": activity.id,
        })

    def _run(self, lines):
        run = self.Run.create({
            "name": "ABC",
            "line_ids": [(0, 0, ln) for ln in lines],
        })
        run.action_compute()
        return run

    def _res(self, run, product):
        return run.result_ids.filtered(lambda r: r.product_id == product)

    # single activity: pool 1000, drivers 60/40 -> 600/400
    def test_single_activity(self):
        self._pool(self.a, 1000)
        run = self._run([
            {"activity_id": self.a.id, "product_id": self.p1.id, "driver_qty": 60},
            {"activity_id": self.a.id, "product_id": self.p2.id, "driver_qty": 40},
        ])
        self.assertEqual(self._res(run, self.p1).amount, 600.0)
        self.assertEqual(self._res(run, self.p2).amount, 400.0)
        self.assertEqual(run.state, "done")

    # two activities sum on each product
    def test_multi_activity(self):
        self._pool(self.a, 1000)
        self._pool(self.b, 500)
        run = self._run([
            {"activity_id": self.a.id, "product_id": self.p1.id, "driver_qty": 60},
            {"activity_id": self.a.id, "product_id": self.p2.id, "driver_qty": 40},
            {"activity_id": self.b.id, "product_id": self.p1.id, "driver_qty": 50},
            {"activity_id": self.b.id, "product_id": self.p2.id, "driver_qty": 50},
        ])
        self.assertEqual(self._res(run, self.p1).amount, 850.0)  # 600 + 250
        self.assertEqual(self._res(run, self.p2).amount, 650.0)  # 400 + 250

    # conservation: awkward split re-sums exactly to the pool
    def test_conservation(self):
        self._pool(self.a, 100)
        run = self._run([
            {"activity_id": self.a.id, "product_id": self.p1.id, "driver_qty": 1},
            {"activity_id": self.a.id, "product_id": self.p2.id, "driver_qty": 2},
        ])
        total = sum(run.result_ids.mapped("amount"))
        self.assertEqual(total, 100.0)

    # pool read as magnitude regardless of sign (costs may be negative)
    def test_negative_amount_pool(self):
        self._pool(self.a, -1000)
        run = self._run([
            {"activity_id": self.a.id, "product_id": self.p1.id, "driver_qty": 60},
            {"activity_id": self.a.id, "product_id": self.p2.id, "driver_qty": 40},
        ])
        self.assertEqual(self._res(run, self.p1).amount, 600.0)

    # empty pool -> zero results, no crash
    def test_empty_pool(self):
        run = self._run([
            {"activity_id": self.a.id, "product_id": self.p1.id, "driver_qty": 60},
        ])
        self.assertEqual(self._res(run, self.p1).amount, 0.0)

    # recompute is idempotent (no duplicated results)
    def test_recompute_idempotent(self):
        self._pool(self.a, 1000)
        run = self._run([
            {"activity_id": self.a.id, "product_id": self.p1.id, "driver_qty": 60},
            {"activity_id": self.a.id, "product_id": self.p2.id, "driver_qty": 40},
        ])
        n1 = len(run.result_ids)
        run.action_compute()
        self.assertEqual(len(run.result_ids), n1)
        self.assertEqual(self._res(run, self.p1).amount, 600.0)

    # driver quantity must be positive
    def test_qty_positive(self):
        with self.assertRaises(Exception):
            with self.env.cr.savepoint():
                self.Run.create({
                    "name": "bad",
                    "line_ids": [(0, 0, {
                        "activity_id": self.a.id, "product_id": self.p1.id,
                        "driver_qty": 0,
                    })],
                })
                self.env.flush_all()
