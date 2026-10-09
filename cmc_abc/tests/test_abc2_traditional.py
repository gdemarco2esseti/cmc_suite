"""WP-07 / ABC-11 - ABC vs traditional volume-based cost per product.

CONTRACT (see abc2_common for the whole WP)
- ``traditional_basis = 'volume'`` + ``traditional_driver_id``: compared objects = results with ACTUAL
  facts of that driver in the span; traditional cost_i = ``traced_cost``_i + pool x qty_i / Σ qty over the
  compared objects, where pool (RN-001, Kaplan & Cooper: distortion is zero-sum) = the overhead ABC
  actually assigns to the compared objects = Σ (amount - traced_cost) over them. Unused capacity,
  business-sustaining and customer/channel costs that never reach the compared objects are NOT in the
  pool, so Σ traditional = Σ ABC over the compared objects. With ``compute_burdens`` the comparison is
  on ``full_cost`` (distortion = (full_cost - trad) / trad) and the pool includes the same burdens
  (Σ full_cost - traced), so Σ traditional = Σ full_cost over the compared objects.
- ``traditional_basis = 'rate'`` + ``traditional_version_id`` (approved ``cmc.plan.version``, AD-108):
  traditional cost = ``cmc.rate._cmc_lookup(cost object, output_measure_id, date_from, origin=version)``
  value x ``quantity``; Compute records ``cmc.run.usage`` (consumer run, origin version), so the version
  cannot be reset while the run is computed (AD-002).
- Per result: ``traditional_cost``, ``distortion_percent`` = (amount - traditional) / traditional x 100,
  ``costing_flag`` ``over`` (traditional > ABC: volume base over-costs), ``under``, ``aligned``
  (|distortion| < 1); objects without a traditional figure: 0 / False.
Hand numbers (RN-001): pool = (7758 - 300) + (5172 - 200) = 12430; DLH P1 75 / P2 25 ->
trad P1 300 + 12430 x 0.75 = 9622.5 (ABC 7758: -19.38%, over), P2 200 + 3107.5 = 3307.5 (ABC 5172:
+56.37%, under); Σ trad 12930 = Σ ABC. Rates 120 / 80 per unit x 100 / 50 units -> 12000 / 4000 ->
-35.35% / +29.30%. Burdens on: rate (1070 + 2000) / 13930; full P1 9467.77, P2 6311.84; pool 15279.61
-> trad P1 11759.71, P2 4019.90. Single product with idle capacity: trad = ABC -> aligned.
"""

from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .abc2_common import D_FROM, Abc2Case


@tagged("post_install", "-at_install")
class TestAbc2Traditional(Abc2Case):
    def test_abc11_volume_based_comparison(self):
        # ABC-11: high-volume P1 is over-costed by a labour-hour base, low-volume P2 under-costed
        run = self._main_run(traditional_basis="volume", traditional_driver_id=self.dlh.id)
        p1, p2 = self._res(run, self.obj_p1), self._res(run, self.obj_p2)
        self.assertAlmostEqual(p1.traditional_cost, 9622.5, places=2)
        self.assertAlmostEqual(p1.distortion_percent, -19.38, places=2)
        self.assertEqual(p1.costing_flag, "over")
        self.assertAlmostEqual(p2.traditional_cost, 3307.5, places=2)
        self.assertAlmostEqual(p2.distortion_percent, 56.37, places=2)
        self.assertEqual(p2.costing_flag, "under")

    def test_rn001_distortion_is_zero_sum(self):
        # RN-001 (Kaplan & Cooper): over- and under-costing cancel out - Σ traditional = Σ ABC compared
        run = self._main_run(traditional_basis="volume", traditional_driver_id=self.dlh.id)
        compared = self._res(run, self.obj_p1) | self._res(run, self.obj_p2)
        self.assertAlmostEqual(sum(compared.mapped("traditional_cost")), sum(compared.mapped("amount")), places=2)
        self.assertAlmostEqual(sum(compared.mapped("traditional_cost")), 12930.0, places=2)

    def test_rn001_pool_excludes_cost_never_reaching_products(self):
        # RN-001: unused capacity 1070, sustaining 2000, customer 1000 never reach P1/P2: not in their pool
        run = self._main_run(traditional_basis="volume", traditional_driver_id=self.dlh.id)
        compared = self._res(run, self.obj_p1) | self._res(run, self.obj_p2)
        self.assertLess(sum(compared.mapped("traditional_cost")), run.total_resource_cost)
        self.assertAlmostEqual(
            sum(compared.mapped("traditional_cost")),
            run.total_resource_cost + run.total_traced_cost - run.unused_capacity_cost - run.sustaining_cost
            - sum((self._res(run, self.obj_c1) | self._res(run, self.obj_c2)).mapped("amount")) - run.unallocated_amount,
            places=2,
        )

    def test_rn001_burdens_compare_full_cost(self):
        # RN-001: with burdens on, full cost is compared with a traditional pool carrying the same burdens
        run = self._main_run(traditional_basis="volume", traditional_driver_id=self.dlh.id, compute_burdens=True)
        p1, p2 = self._res(run, self.obj_p1), self._res(run, self.obj_p2)
        self.assertAlmostEqual(p1.traditional_cost + p2.traditional_cost, p1.full_cost + p2.full_cost, places=2)
        self.assertAlmostEqual(p1.traditional_cost, 11759.71, places=1)
        self.assertAlmostEqual(p2.traditional_cost, 4019.90, places=1)
        for res in (p1, p2):
            expected = (res.full_cost - res.traditional_cost) / res.traditional_cost * 100.0
            self.assertAlmostEqual(res.distortion_percent, expected, places=2)
        self.assertEqual((p1.costing_flag, p2.costing_flag), ("over", "under"))

    def test_abc11_no_traditional_figure_for_customers(self):
        # ABC-11: a customer has no labour hours - no comparison, not a 100% distortion
        run = self._main_run(traditional_basis="volume", traditional_driver_id=self.dlh.id)
        c1 = self._res(run, self.obj_c1)
        self.assertAlmostEqual(c1.traditional_cost, 0.0, places=2)
        self.assertFalse(c1.costing_flag)

    def _approved_version(self):
        env = self.env_a
        version = env["cmc.plan.version"].create(
            {"name": "WP07 standard costs", "date_from": D_FROM, "date_to": D_FROM.replace(month=12, day=31),
             "company_id": self.company_a.id}
        )
        env["cmc.rate"]._cmc_publish(version, [
            {"holder": self.obj_p1, "driver": self.produced, "date": D_FROM, "value": 120.0},
            {"holder": self.obj_p2, "driver": self.produced, "date": D_FROM, "value": 80.0},
        ])
        version.sudo().action_approve()
        return version

    def test_abc11_rate_based_comparison(self):
        # ABC-11 / AD-108: traditional unit costs read from the rate registry (approved standard version)
        version = self._approved_version()
        run = self._main_run(traditional_basis="rate", traditional_version_id=version.id)
        p1, p2 = self._res(run, self.obj_p1), self._res(run, self.obj_p2)
        self.assertAlmostEqual(p1.traditional_cost, 12000.0, places=2)
        self.assertAlmostEqual(p1.distortion_percent, -35.35, places=2)
        self.assertAlmostEqual(p2.traditional_cost, 4000.0, places=2)
        self.assertAlmostEqual(p2.distortion_percent, 29.30, places=2)

    def test_abc11_rate_source_is_a_recorded_usage(self):
        # AD-002: the version the comparison read cannot be reopened under a computed run
        version = self._approved_version()
        run = self._main_run(traditional_basis="rate", traditional_version_id=version.id)
        usage = self.env["cmc.run.usage"].sudo().search(
            [("consumer_model", "=", "cmc.abc.run"), ("consumer_id", "=", run.id)]
        )
        self.assertEqual(set(zip(usage.mapped("origin_model"), usage.mapped("origin_id"))),
                         {("cmc.plan.version", version.id)})
        with self.assertRaises(UserError):
            version.sudo().action_reset_to_draft()
        run.action_reset_to_draft()
        version.sudo().action_reset_to_draft()
        self.assertEqual(version.state, "draft")


@tagged("post_install", "-at_install")
class TestAbc2TraditionalSingleProduct(Abc2Case):
    """RN-001: one product, one activity with idle capacity (1000 F / 50 h = 20, used 40 h -> 800,
    unused 200). The only product cannot be distorted against itself: traditional = ABC = 800."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env_a
        center = cls._center(env, "WP07 mono", cls.company_a)
        cls._cost(env, center, 1000, cls.oth, "fixed")
        cls.rm = env["cmc.abc.resource"].create({"name": "Mono resource", "code": "WP07RM", "center_id": center.id})
        cls.mono = cls._activity("Run mono line", "WP07MONO", cls.pr_mfg, "unit", cls.mhours)
        cls.p3 = cls.env["product.product"].create({"name": "WP07 P3"})
        cls.obj_p3 = env["cmc.cost.object"]._cmc_ensure_objects(cls.p3, company=cls.company_a)
        cls._fact(env, cls.dlh, cls.obj_p3, 10)

    def test_rn001_single_product_with_idle_capacity_is_aligned(self):
        # RN-001: unused capacity is not product cost - a single product plant shows no distortion
        run = self._main_run(
            trace_direct=False, output_measure_id=False,
            resource_lines=[{"resource_id": self.rm.id, "activity_id": self.mono.id, "percent": 100}],
            edges=[{"activity_id": self.mono.id, "cost_object_id": self.obj_p3.id, "driver_id": self.mhours.id, "driver_qty": 40}],
            capacity=[{"activity_id": self.mono.id, "practical_capacity": 50}],
            traditional_basis="volume", traditional_driver_id=self.dlh.id,
        )
        p3 = self._res(run, self.obj_p3)
        self.assertAlmostEqual(run.unused_capacity_cost, 200.0, places=2)
        self.assertAlmostEqual(p3.amount, 800.0, places=2)
        self.assertAlmostEqual(p3.traditional_cost, 800.0, places=2)
        self.assertAlmostEqual(p3.distortion_percent, 0.0, places=2)
        self.assertEqual(p3.costing_flag, "aligned")
