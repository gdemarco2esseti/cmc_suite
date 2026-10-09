"""WP-07 / ABC-04 (practical capacity), ABC-10 (sustaining object, idle and SG&A burdens, F/V).

CONTRACT (see abc2_common for the whole WP)
- ``capacity_line_ids`` (``cmc.abc.capacity``: ``activity_id``, ``practical_capacity``): two-tier rate
  (Kaplan Ch.14) ``fixed_rate`` = fixed cost / practical capacity, ``variable_rate`` = variable cost / used,
  ``rate`` = sum; ``unused_cost`` = (capacity - used) x fixed_rate -> flow stage ``unused`` (no target),
  ``run.unused_capacity_cost``; never charged to an object. used > practical capacity -> UserError.
- ``is_sustaining`` activities -> 100% to ``run.sustaining_object_id`` (``cmc.cost.object`` type
  ``sustaining``, res_model ``res.company``, res_id = run company), flow stage ``sustaining``,
  ``run.sustaining_cost``; an edge from a sustaining activity -> UserError.
- ``compute_burdens`` (Frost St.6): ``idle_burden_rate`` = unused / Σ final object ABC x 100,
  ``sustaining_burden_rate`` = sustaining / Σ final object ABC x 100; per result ``idle_burden`` /
  ``sustaining_burden`` = amount x rate, ``full_cost`` = amount + burdens (a view: ``amount`` and the
  conservation identity do not change). Off: burdens 0, ``full_cost`` = ``amount``.
"""

from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .abc2_common import Abc2Case


@tagged("post_install", "-at_install")
class TestAbc2Capacity(Abc2Case):
    def test_abc04_rate_on_practical_capacity(self):
        # ABC-04/CAP (Kaplan Ch.7, Ch.14): 5350 F / 125 h = 42.8 + 2000 V / 100 h = 20 -> 62.8
        run = self._main_run()
        mach = self._act(run, self.mach)
        self.assertAlmostEqual(mach.practical_capacity, 125.0)
        self.assertAlmostEqual(mach.used_qty, 100.0)
        self.assertAlmostEqual(mach.fixed_rate, 42.8, places=4)
        self.assertAlmostEqual(mach.variable_rate, 20.0, places=4)
        self.assertAlmostEqual(mach.rate, 62.8, places=4)
        self.assertAlmostEqual(mach.charged_cost, 6280.0, places=2)
        self.assertAlmostEqual(mach.unused_cost, 1070.0, places=2)
        self.assertAlmostEqual(run.unused_capacity_cost, 1070.0, places=2)

    def test_abc04_unused_capacity_not_charged(self):
        # Kaplan Ch.7: unused capacity is reported, never allocated back to products
        run = self._main_run()
        to_objects = run.flow_ids.filtered(lambda f: f.source_activity_id == self.mach and f.target_object_id)
        self.assertAlmostEqual(sum(to_objects.mapped("amount")), 6280.0, places=2)
        unused = run.flow_ids.filtered(lambda f: f.stage == "unused")
        self.assertEqual(unused.source_activity_id, self.mach)
        self.assertFalse(unused.target_object_id)
        self.assertAlmostEqual(sum(unused.mapped("fixed_amount")), 1070.0, places=2)
        self.assertAlmostEqual(sum(unused.mapped("variable_amount")), 0.0, places=2)

    def test_abc04_fixed_rate_stable_when_volume_drops(self):
        # Kaplan Ch.7 (no death spiral): half the hours, same fixed rate, more unused capacity
        edges = [e for e in self._edges() if e.get("activity_id") != self.mach.id] + [
            {"activity_id": self.mach.id, "cost_object_id": self.obj_p1.id, "driver_id": self.mhours.id, "driver_qty": 30},
            {"activity_id": self.mach.id, "cost_object_id": self.obj_p2.id, "driver_id": self.mhours.id, "driver_qty": 20},
        ]
        run = self._main_run(edges=edges)
        mach = self._act(run, self.mach)
        self.assertAlmostEqual(mach.fixed_rate, 42.8, places=4)
        self.assertAlmostEqual(mach.unused_cost, 3210.0, places=2)  # 75 h x 42.8

    def test_abc04_used_above_capacity_fails_loud(self):
        # CAP: a practical capacity below the measured use is a wrong estimate, not a negative unused cost
        run = self._main_run(compute=False, capacity=[{"activity_id": self.mach.id, "practical_capacity": 90}])
        with self.assertRaisesRegex(UserError, "Run machines"):
            run.action_compute()

    def test_abc04_kaplan_order_handling(self):
        # Kaplan Ch.7 textbook: 280,000 / 5,000 orders = 56 (not 70 at 4,000); unused 1,000 x 56 = 56,000
        env = self.env_a
        center = self._center(env, "WP07 order desk", self.company_a)
        self._cost(env, center, 280000, self.oth, "fixed")
        resource = env["cmc.abc.resource"].create({"name": "Order desk", "code": "WP07ROD", "center_id": center.id})
        orders = self._activity("Handle orders", "WP07HORD", self.pr_sup, "customer", self.orders)
        run = self._main_run(
            trace_direct=False, output_measure_id=False,
            resource_lines=[{"resource_id": resource.id, "activity_id": orders.id, "percent": 100}],
            edges=[
                {"activity_id": orders.id, "cost_object_id": self.obj_c1.id, "driver_id": self.orders.id, "driver_qty": 3000},
                {"activity_id": orders.id, "cost_object_id": self.obj_c2.id, "driver_id": self.orders.id, "driver_qty": 1000},
            ],
            capacity=[{"activity_id": orders.id, "practical_capacity": 5000}],
        )
        self.assertAlmostEqual(self._act(run, orders).rate, 56.0, places=4)
        self.assertAlmostEqual(self._res(run, self.obj_c1).amount, 168000.0, places=2)
        self.assertAlmostEqual(self._res(run, self.obj_c2).amount, 56000.0, places=2)
        self.assertAlmostEqual(run.unused_capacity_cost, 56000.0, places=2)


@tagged("post_install", "-at_install")
class TestAbc2Sustaining(Abc2Case):
    def test_abc10_sustaining_object(self):
        # ABC-10: facility-sustaining cost lands on the business-sustaining object, not on products
        run = self._main_run()
        obj = run.sustaining_object_id
        self.assertEqual(obj.object_type, "sustaining")
        self.assertEqual((obj.res_model, obj.res_id), ("res.company", self.company_a.id))
        flows = run.flow_ids.filtered(lambda f: f.stage == "sustaining")
        self.assertEqual(flows.source_activity_id, self.mgmt)
        self.assertEqual(flows.target_object_id, obj)
        self.assertAlmostEqual(run.sustaining_cost, 2000.0, places=2)
        products = run.flow_ids.filtered(lambda f: f.source_activity_id == self.mgmt and f.target_object_id != obj)
        self.assertFalse(products)

    def test_abc10_edge_from_sustaining_activity_fails(self):
        # ABC-10: charging business-sustaining cost to a product is exactly the distortion ABC removes
        edges = self._edges() + [
            {"activity_id": self.mgmt.id, "cost_object_id": self.obj_p1.id, "driver_qty": 1},
        ]
        run = self._main_run(compute=False, edges=edges)
        with self.assertRaises(UserError):
            run.action_compute()

    def test_abc10_sga_activity_marked_sustaining(self):
        # ABC-10 configurable: SG&A (serve customers) kept off the objects when marked sustaining
        self.cust.is_sustaining = True
        edges = [e for e in self._edges() if e.get("activity_id") != self.cust.id]
        run = self._main_run(edges=edges)
        self.assertAlmostEqual(run.sustaining_cost, 3000.0, places=2)
        self.assertAlmostEqual(sum(self._res(run, self.obj_c1).mapped("amount")), 0.0, places=2)

    def test_abc10_burdens_off_by_default(self):
        # ABC-10: full cost = ABC cost unless burdens are asked for
        run = self._main_run()
        p1 = self._res(run, self.obj_p1)
        self.assertAlmostEqual(p1.idle_burden, 0.0, places=2)
        self.assertAlmostEqual(p1.full_cost, p1.amount, places=2)


@tagged("post_install", "-at_install")
class TestAbc2Burdens(Abc2Case):
    """Frost St.6 textbook: ABC 90 + idle burden 13.5 + SG&A burden 13.5 = 117 (rates 15%).

    Center F 1170 fixed, resource RF by quantity: PROC 1035, MGMT 135. PROC: X 2 h, Y 18 h, practical
    capacity 23 h -> rate 45, X 90, Y 810, unused 3 x 45 = 135. Σ ABC 900; idle 135 / 900 = 15%,
    sustaining 135 / 900 = 15%. The v1 QC tag 500 has no edge here -> unallocated, outside the burdens.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env_a
        center = cls._center(env, "WP07 F", cls.company_a)
        cls._cost(env, center, 1170, cls.oth, "fixed")
        cls.rf = env["cmc.abc.resource"].create(
            {"name": "F resource", "code": "WP07RF", "center_id": center.id, "driver_basis": "quantity"}
        )
        cls.proc = cls._activity("Process items", "WP07PROC", cls.pr_mfg, "unit", cls.mhours)
        Product = cls.env["product.product"]
        cls.px, cls.py = Product.create([{"name": "WP07 X"}, {"name": "WP07 Y"}])
        cls.obj_x, cls.obj_y = env["cmc.cost.object"]._cmc_ensure_objects(cls.px | cls.py, company=cls.company_a)

    def _burden_run(self, **vals):
        return self._main_run(
            trace_direct=False, output_measure_id=False,
            resource_lines=[
                {"resource_id": self.rf.id, "activity_id": self.proc.id, "quantity": 1035},
                {"resource_id": self.rf.id, "activity_id": self.mgmt.id, "quantity": 135},
            ],
            edges=[
                {"activity_id": self.proc.id, "cost_object_id": self.obj_x.id, "driver_id": self.mhours.id, "driver_qty": 2},
                {"activity_id": self.proc.id, "cost_object_id": self.obj_y.id, "driver_id": self.mhours.id, "driver_qty": 18},
            ],
            capacity=[{"activity_id": self.proc.id, "practical_capacity": 23}],
            **vals,
        )

    def test_abc10_frost_burdens(self):
        # ABC-10: full service cost = ABC + idle burden + SG&A burden (90 + 13.5 + 13.5 = 117)
        run = self._burden_run(compute_burdens=True)
        self.assertAlmostEqual(run.idle_burden_rate, 15.0, places=4)
        self.assertAlmostEqual(run.sustaining_burden_rate, 15.0, places=4)
        x = self._res(run, self.obj_x)
        self.assertAlmostEqual(x.amount, 90.0, places=2)
        self.assertAlmostEqual(x.idle_burden, 13.5, places=2)
        self.assertAlmostEqual(x.sustaining_burden, 13.5, places=2)
        self.assertAlmostEqual(x.full_cost, 117.0, places=2)
        self.assertAlmostEqual(self._res(run, self.obj_y).full_cost, 1053.0, places=2)

    def test_abc10_burdens_absorb_exactly(self):
        # ABC-10 absorption = sustaining x ABC_i / Σ ABC: the burdens re-sum to unused + sustaining
        run = self._burden_run(compute_burdens=True)
        results = run.result_ids
        self.assertAlmostEqual(sum(results.mapped("idle_burden")), run.unused_capacity_cost, places=2)
        self.assertAlmostEqual(sum(results.mapped("sustaining_burden")), run.sustaining_cost, places=2)
        self.assertAlmostEqual(sum(results.mapped("full_cost")), 1170.0, places=2)
        self.assertAlmostEqual(run.unallocated_amount, 500.0, places=2)
        # a view: the ABC amounts and the conservation identity are untouched
        self.assertAlmostEqual(run.total_object_cost, 900.0, places=2)
