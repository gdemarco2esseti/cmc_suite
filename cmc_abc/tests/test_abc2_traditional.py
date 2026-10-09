"""WP-07 / ABC-11 - ABC vs traditional volume-based cost per product.

CONTRACT (see abc2_common for the whole WP)
- ``traditional_basis = 'volume'`` + ``traditional_driver_id``: traditional cost of an object with
  ACTUAL facts of that driver in the span = ``traced_cost`` + ``total_resource_cost`` x qty_i / Σ qty
  (one volume base absorbs every overhead the ABC model spreads: trad = OH / DLH x DLH).
- ``traditional_basis = 'rate'`` + ``traditional_version_id`` (approved ``cmc.plan.version``, AD-108):
  traditional cost = ``cmc.rate._cmc_lookup(cost object, output_measure_id, date_from, origin=version)``
  value x ``quantity``; Compute records ``cmc.run.usage`` (consumer run, origin version), so the version
  cannot be reset while the run is computed (AD-002).
- Per result: ``traditional_cost``, ``distortion_percent`` = (amount - traditional) / traditional x 100,
  ``costing_flag`` ``over`` (traditional > ABC: volume base over-costs), ``under``, ``aligned``
  (|distortion| < 1); objects without a traditional figure: 0 / False.
Hand numbers: DLH P1 75 / P2 25 -> trad P1 300 + 16500 x 0.75 = 12675 (ABC 7758: -38.79%, over),
P2 200 + 4125 = 4325 (ABC 5172: +19.58%, under). Rates 120 / 80 per unit x 100 / 50 units ->
12000 / 4000 -> -35.35% / +29.30%.
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
        self.assertAlmostEqual(p1.traditional_cost, 12675.0, places=2)
        self.assertAlmostEqual(p1.distortion_percent, -38.79, places=2)
        self.assertEqual(p1.costing_flag, "over")
        self.assertAlmostEqual(p2.traditional_cost, 4325.0, places=2)
        self.assertAlmostEqual(p2.distortion_percent, 19.58, places=2)
        self.assertEqual(p2.costing_flag, "under")

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
