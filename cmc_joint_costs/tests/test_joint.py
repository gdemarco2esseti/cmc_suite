import psycopg2

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged
from odoo.tools import mute_logger


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

    # by-product NRV nets the pool: C nrv = 10*5 - 20 = 30 -> mains split 970
    def test_byproduct_netting(self):
        run = self._run("physical", 1000, [
            {"product_id": self.pA.id, "quantity": 60},
            {"product_id": self.pB.id, "quantity": 40},
            {"product_id": self.pC.id, "quantity": 10, "unit_sales_value": 5,
             "separable_cost": 20, "is_byproduct": True},
        ])
        self.assertEqual(self._res(run, self.pA).allocated_joint_cost, 582.0)
        self.assertEqual(self._res(run, self.pB).allocated_joint_cost, 388.0)
        # by-product carries its own NRV, and total = NRV + separable cost
        c = self._res(run, self.pC)
        self.assertEqual(c.allocated_joint_cost, 30.0)
        self.assertEqual(c.separable_cost, 20.0)
        self.assertEqual(c.total_cost, 50.0)
        self.assertEqual(c.unit_cost, 5.0)
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
        run.action_reset_to_draft()  # AD-001: recompute goes through a reset
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
    @mute_logger("odoo.sql_db")
    def test_qty_positive(self):
        with self.assertRaises(psycopg2.errors.CheckViolation):
            with self.env.cr.savepoint():
                self.Run.create({
                    "name": "bad", "method": "physical", "joint_cost": 100,
                    "line_ids": [(0, 0, {"product_id": self.pA.id, "quantity": 0})],
                })
                self.env.flush_all()

    # a negative joint cost pool is rejected at the database level, instead of
    # surfacing later as the misleading "by-product NRV exceeds the pool" error
    @mute_logger("odoo.sql_db")
    def test_joint_cost_non_negative(self):
        with self.assertRaises(psycopg2.errors.CheckViolation):
            with self.env.cr.savepoint():
                self.Run.create({
                    "name": "bad", "method": "physical", "joint_cost": -500,
                })
                self.env.flush_all()

    @mute_logger("odoo.sql_db")
    def test_line_amounts_non_negative(self):
        for field in ("unit_sales_value", "separable_cost"):
            with self.subTest(field=field):
                with self.assertRaises(psycopg2.errors.CheckViolation):
                    with self.env.cr.savepoint():
                        self.Run.create({
                            "name": "bad", "method": "sales_value",
                            "joint_cost": 100,
                            "line_ids": [(0, 0, {
                                "product_id": self.pA.id, "quantity": 1,
                                field: -10,
                            })],
                        })
                        self.env.flush_all()

    # constant margin cannot hand a product a NEGATIVE share of the pool:
    # A sells 100 but costs 900 to finish, so gm goes below zero
    def test_constant_gm_negative_allocation_raises(self):
        with self.assertRaises(UserError):
            self._run("constant_gm", 1000, [
                {"product_id": self.pA.id, "quantity": 1,
                 "unit_sales_value": 100, "separable_cost": 900},
                {"product_id": self.pB.id, "quantity": 1,
                 "unit_sales_value": 1000},
            ])

    # unit cost is a rate, not an amount: 4200 over 1M units is 0.0042, and it
    # must survive the write (the currency's 2 decimals would store 0.00)
    def test_unit_cost_below_currency_rounding(self):
        precision = self.env["decimal.precision"].search(
            [("name", "=", "Product Price")]
        )
        precision.digits = 6
        # the ormcache is global: drop it again so later tests re-read the db
        self.addCleanup(self.env.registry.clear_cache, "stable")
        run = self._run("physical", 4200, [
            {"product_id": self.pA.id, "quantity": 1_000_000},
        ])
        result = self._res(run, self.pA)
        self.assertEqual(result.total_cost, 4200.0)
        self.assertEqual(result.unit_cost, 0.0042)

    # editing an input after the run is done must not leave stale results
    # vouched for by a "done" badge
    def test_line_change_after_reset(self):
        run = self._run("physical", 1000, [
            {"product_id": self.pA.id, "quantity": 60},
            {"product_id": self.pB.id, "quantity": 40},
        ])
        self.assertEqual(run.state, "done")
        run.action_reset_to_draft()
        self.assertFalse(run.result_ids)
        run.line_ids.filtered(lambda l: l.product_id == self.pA).quantity = 90
        run.action_compute()
        self.assertEqual(self._res(run, self.pA).allocated_joint_cost, 692.31)

    # AD-001: a computed run is locked (it used to reopen itself silently)
    def test_line_unlink_locked_when_done(self):
        run = self._run("physical", 1000, [
            {"product_id": self.pA.id, "quantity": 60},
            {"product_id": self.pB.id, "quantity": 40},
        ])
        with self.assertRaises(UserError):
            run.line_ids.filtered(lambda l: l.product_id == self.pB).unlink()
        self.assertEqual(run.state, "done")
        self.assertEqual(len(run.result_ids), 2)

    def test_line_add_locked_when_done(self):
        run = self._run("physical", 1000, [
            {"product_id": self.pA.id, "quantity": 60},
            {"product_id": self.pB.id, "quantity": 40},
        ])
        with self.assertRaises(UserError):
            run.write({
                "line_ids": [(0, 0, {"product_id": self.pC.id, "quantity": 100})],
            })
        with self.assertRaises(UserError):
            self.env["cmc.joint.line"].create(
                {"run_id": run.id, "product_id": self.pC.id, "quantity": 100}
            )
        with self.assertRaises(UserError):
            run.line_ids[:1].quantity = 10

    def test_header_locked_when_done(self):
        run = self._run("physical", 1000, [
            {"product_id": self.pA.id, "quantity": 60},
            {"product_id": self.pB.id, "quantity": 40},
        ])
        with self.assertRaises(UserError):
            run.method = "sales_value"
        with self.assertRaises(UserError):
            run.joint_cost = 2000

    def test_action_reset_to_draft(self):
        run = self._run("physical", 1000, [
            {"product_id": self.pA.id, "quantity": 60},
            {"product_id": self.pB.id, "quantity": 40},
        ])
        run.action_reset_to_draft()
        self.assertEqual(run.state, "draft")
        self.assertFalse(run.result_ids)
