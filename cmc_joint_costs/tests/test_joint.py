from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestJoint(TransactionCase):
    """Fase 3+ — joint cost allocation (physical / sales value / NRV /
    constant gross margin) + by-product NRV netting."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Run = cls.env["cmc.joint.run"]
        cls.pA = cls.env["product.product"].create({"name": "A"})
        cls.pB = cls.env["product.product"].create({"name": "B"})
        cls.pC = cls.env["product.product"].create({"name": "C"})

    def _run(self, method, joint_cost, lines):
        run = self.Run.create({
            "name": "JC", "method": method, "joint_cost": joint_cost,
            "line_ids": [(0, 0, ln) for ln in lines],
        })
        run.action_compute()
        return run

    def _res(self, run, product):
        return run.result_ids.filtered(lambda r: r.product_id == product)

    # physical units: 1000 split 60/40 -> 600/400
    def test_physical(self):
        run = self._run("physical", 1000, [
            {"product_id": self.pA.id, "quantity": 60},
            {"product_id": self.pB.id, "quantity": 40},
        ])
        self.assertEqual(self._res(run, self.pA).allocated_joint_cost, 600.0)
        self.assertEqual(self._res(run, self.pB).allocated_joint_cost, 400.0)
        self.assertEqual(run.state, "done")

    # sales value at split-off: sales 600 vs 800 -> 1000 * 600/1400, 800/1400
    def test_sales_value(self):
        run = self._run("sales_value", 1000, [
            {"product_id": self.pA.id, "quantity": 60, "unit_sales_value": 10},
            {"product_id": self.pB.id, "quantity": 40, "unit_sales_value": 20},
        ])
        a = self._res(run, self.pA).allocated_joint_cost
        b = self._res(run, self.pB).allocated_joint_cost
        self.assertAlmostEqual(a, 428.57, 2)
        self.assertAlmostEqual(b, 571.43, 2)
        self.assertEqual(a + b, 1000.0)  # conservation

    # NRV: A 900-100=800, B 1000-200=800 -> 500/500
    def test_nrv(self):
        run = self._run("nrv", 1000, [
            {"product_id": self.pA.id, "quantity": 60, "unit_sales_value": 15,
             "separable_cost": 100},
            {"product_id": self.pB.id, "quantity": 40, "unit_sales_value": 25,
             "separable_cost": 200},
        ])
        self.assertEqual(self._res(run, self.pA).allocated_joint_cost, 500.0)
        self.assertEqual(self._res(run, self.pB).allocated_joint_cost, 500.0)

    # constant gross margin: gm = 600/1900; A=515.79, B=484.21
    def test_constant_gm(self):
        run = self._run("constant_gm", 1000, [
            {"product_id": self.pA.id, "quantity": 60, "unit_sales_value": 15,
             "separable_cost": 100},  # sales 900
            {"product_id": self.pB.id, "quantity": 40, "unit_sales_value": 25,
             "separable_cost": 200},  # sales 1000
        ])
        a = self._res(run, self.pA).allocated_joint_cost
        b = self._res(run, self.pB).allocated_joint_cost
        self.assertAlmostEqual(a, 515.79, 2)
        self.assertAlmostEqual(b, 484.21, 2)
        self.assertEqual(a + b, 1000.0)

    # by-product NRV nets the pool: C nrv 50 -> mains split 950 (physical 60/40)
    def test_byproduct_netting(self):
        run = self._run("physical", 1000, [
            {"product_id": self.pA.id, "quantity": 60},
            {"product_id": self.pB.id, "quantity": 40},
            {"product_id": self.pC.id, "quantity": 10, "unit_sales_value": 5,
             "is_byproduct": True},
        ])
        self.assertEqual(self._res(run, self.pA).allocated_joint_cost, 570.0)
        self.assertEqual(self._res(run, self.pB).allocated_joint_cost, 380.0)
        # by-product carries its own NRV
        c = self._res(run, self.pC)
        self.assertEqual(c.allocated_joint_cost, 50.0)
        self.assertTrue(c.is_byproduct)

    # unit cost = (allocated + separable) / qty
    def test_unit_cost(self):
        run = self._run("nrv", 1000, [
            {"product_id": self.pA.id, "quantity": 60, "unit_sales_value": 15,
             "separable_cost": 100},
            {"product_id": self.pB.id, "quantity": 40, "unit_sales_value": 25,
             "separable_cost": 200},
        ])
        rA = self._res(run, self.pA)
        # allocated 500 + separable 100 = 600 total, /60 = 10
        self.assertEqual(rA.total_cost, 600.0)
        self.assertEqual(rA.unit_cost, 10.0)

    # recompute idempotent
    def test_recompute_idempotent(self):
        run = self._run("physical", 1000, [
            {"product_id": self.pA.id, "quantity": 60},
            {"product_id": self.pB.id, "quantity": 40},
        ])
        n1 = len(run.result_ids)
        run.action_compute()
        self.assertEqual(len(run.result_ids), n1)
        self.assertEqual(self._res(run, self.pA).allocated_joint_cost, 600.0)

    # guard: NRV base non-positive (separable >= revenue on all mains) -> raise
    def test_nrv_non_positive_base_raises(self):
        with self.assertRaises(UserError):
            self._run("nrv", 1000, [
                {"product_id": self.pA.id, "quantity": 60, "unit_sales_value": 5,
                 "separable_cost": 400},
                {"product_id": self.pB.id, "quantity": 40, "unit_sales_value": 5,
                 "separable_cost": 300},
            ])

    # guard: by-product NRV exceeding the pool -> raise (no negative costs)
    def test_byproduct_exceeds_pool_raises(self):
        with self.assertRaises(UserError):
            self._run("physical", 100, [
                {"product_id": self.pA.id, "quantity": 60},
                {"product_id": self.pC.id, "quantity": 10, "unit_sales_value": 50,
                 "is_byproduct": True},  # NRV 500 > pool 100
            ])

    # guard: constant_gm with zero total sales -> raise
    def test_constant_gm_zero_sales_raises(self):
        with self.assertRaises(UserError):
            self._run("constant_gm", 1000, [
                {"product_id": self.pA.id, "quantity": 60},
                {"product_id": self.pB.id, "quantity": 40},
            ])

    # quantity must be positive
    def test_qty_positive(self):
        with self.assertRaises(Exception):
            with self.env.cr.savepoint():
                self.Run.create({
                    "name": "bad", "method": "physical", "joint_cost": 100,
                    "line_ids": [(0, 0, {"product_id": self.pA.id, "quantity": 0})],
                })
                self.env.flush_all()
