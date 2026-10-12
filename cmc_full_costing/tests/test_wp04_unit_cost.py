"""WP-04 / FC-05 - produced quantity from the measure facts, unit cost per level, Cu curve, prefill.

CONTRACT
- ``cmc.costing.run.produced_measure_id`` M2o ``cmc.cost.driver``: produced quantity of a product =
  Σ ``cmc.measure.quantity.quantity`` of that measure, ACTUAL (``version_id`` empty), run company,
  ``date`` inside the run period, ``cost_object_id`` = the product's cost object (all centers summed).
  Copied at Compute into ``cmc.costing.result.produced_qty`` (snapshot, AD-107/113). No measure or no
  fact -> ``produced_qty = 0`` and every unit cost 0 (the run still computes).
- ``cmc.costing.result.unit_cost_primo/_industriale/_complessivo/_economico_tecnico`` = level /
  produced_qty; ``cmc.costing.config.unit_amount`` = cumulative level / produced_qty.
- ``cmc.costing.result._cmc_unit_cost_curve(volumes)`` -> list (same order) of dicts
  ``{"quantity", "unit_variable", "unit_fixed", "unit_cost"}`` with Cu = v + CF / Q, v =
  ``unit_cost_variable``, CF = ``cost_fixed``; a volume <= 0 raises UserError.
- ``cmc.costing.run.action_cmc_prefill_lines()`` (draft only, else UserError): replaces the driver lines
  with one line per (final center, product, ALLOCATION measure) = Σ actual facts of the period/company
  with a center and a product cost object; statistical measures (produced qty) are not drivers.
Numbers: P1 100 units (60 Z + 40 W; April 500, other measure 777, budget 999 ignored), P2 50.
P1 unit primo 4.20, industrial 14.40 ; P2 3.00, 18.60. P1 v 5.40, CF 900 -> Cu(50) 23.40,
Cu(100) 14.40, Cu(200) 9.90, Cu(300) 8.40. Cinquini V.1.4: 100,239.43 / 4,620 min = 21.6968 EUR/min.
"""

from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .wp04_common import D_FROM, D_TO, Wp04Case


@tagged("post_install", "-at_install")
class TestWp04UnitCost(Wp04Case):
    def test_fc05_produced_qty_from_actual_facts(self):
        # FC-05: produced qty = actual facts of the measure in the period, all centers, no plan figures
        run = self._run_a()
        self.assertRecordValues(
            self._res(run, self.p1) | self._res(run, self.p2),
            [{"produced_qty": 100.0}, {"produced_qty": 50.0}],
        )

    def test_fc05_unit_cost_per_level(self):
        # FC-05: unit cost = configuration level / produced quantity
        run = self._run_a()
        self.assertRecordValues(
            self._res(run, self.p1) | self._res(run, self.p2),
            [
                {"unit_cost_primo": 4.20, "unit_cost_industriale": 14.40,
                 "unit_cost_complessivo": 14.40, "unit_cost_economico_tecnico": 14.40},
                {"unit_cost_primo": 3.00, "unit_cost_industriale": 18.60,
                 "unit_cost_complessivo": 18.60, "unit_cost_economico_tecnico": 18.60},
            ],
        )
        configs = self._res(run, self.p1).config_ids.sorted("sequence")
        self.assertEqual(configs.mapped("level"), ["primo", "industriale", "complessivo", "economico_tecnico"])
        for config, expected in zip(configs, (4.20, 14.40, 14.40, 14.40)):
            self.assertAlmostEqual(config.unit_amount, expected, places=2)

    def test_fc05_no_fact_gives_zero_unit_cost(self):
        # FC-05: a product with cost but no produced quantity is costed, not divided by zero
        self.fact_p2.unlink()
        run = self._run_a()
        self.assertRecordValues(
            self._res(run, self.p2),
            [{"produced_qty": 0.0, "unit_cost_primo": 0.0, "unit_cost_industriale": 0.0, "cost_industriale": 930.0}],
        )

    def test_fc05_no_measure_no_unit_cost(self):
        # FC-05: without a produced measure the run keeps the v1 totals only
        run = self._run_a(produced_measure_id=False)
        self.assertEqual(run.result_ids.mapped("produced_qty"), [0.0, 0.0])
        self.assertAlmostEqual(self._res(run, self.p1).cost_industriale, 1440.0, places=2)

    def test_fc05_produced_qty_is_snapshot(self):
        # AD-107/113: a later feeder refresh / manual edit never changes a computed run
        run = self._run_a()
        self.fact_p1_z.quantity = 600
        self.env.invalidate_all()
        self.assertAlmostEqual(self._res(run, self.p1).produced_qty, 100.0, places=2)
        self.assertAlmostEqual(self._res(run, self.p1).unit_cost_industriale, 14.40, places=2)

    def test_fc05_unit_cost_curve(self):
        # FC-05 / Cinquini I.4: Cu = v + CF/Q, the instability of the unit cost with volume
        res = self._res(self._run_a(), self.p1)
        curve = res._cmc_unit_cost_curve([50, 100, 200, 300])
        self.assertEqual([pt["quantity"] for pt in curve], [50, 100, 200, 300])
        for pt, (fixed, cu) in zip(curve, ((18.0, 23.40), (9.0, 14.40), (4.5, 9.90), (3.0, 8.40))):
            self.assertAlmostEqual(pt["unit_variable"], 5.40, places=2)
            self.assertAlmostEqual(pt["unit_fixed"], fixed, places=2)
            self.assertAlmostEqual(pt["unit_cost"], cu, places=2)
        # at the actual volume the curve meets the computed unit full cost
        self.assertAlmostEqual(curve[1]["unit_cost"], res.unit_cost_complessivo, places=2)

    def test_fc05_curve_rejects_non_positive_volume(self):
        res = self._res(self._run_a(), self.p1)
        with self.assertRaises(UserError):
            res._cmc_unit_cost_curve([0])

    def test_fc05_homogeneous_output_coefficient_from_facts(self):
        # FC-05 / Cinquini V.1.4: driver qty prefilled from facts, coeff = center cost / homogeneous output
        company = self.env["res.company"].create({"name": "CMC WP04 C"})
        env = self._company_env(company)
        center = self._center(env, "WP04 H", company)
        self._indirect(env, center, 100239.43, "fixed")
        closing = self._closing(env, company)
        minutes = env["cmc.cost.driver"].create({"name": "Standard minutes", "code": "WP04MIN", "company_id": company.id})
        produced = self._measure(env, company, "WP04PQC")
        x, y = self.env["product.product"].create([{"name": "WP04 X"}, {"name": "WP04 Y"}])
        obj_x, obj_y = env["cmc.cost.object"]._cmc_ensure_objects(x | y, company=company)
        # homogeneous output = Σ unit time x units: X 2,000 min, Y 2,620 min (two facts for Y)
        self._fact(env, minutes, obj_x, 2000, center=center)
        self._fact(env, minutes, obj_y, 1620, center=center)
        self._fact(env, minutes, obj_y, 1000, center=center)
        self._fact(env, produced, obj_x, 400, center=center)  # statistical: not a driver line
        run = env["cmc.costing.run"].create({
            "name": "Cinquini V.1.4", "company_id": company.id, "closing_run_id": closing.id,
            "date_from": D_FROM, "date_to": D_TO, "base": "multiple",
            "product_ids": [(6, 0, (x | y).ids)], "produced_measure_id": produced.id,
        })
        run.action_cmc_prefill_lines()
        self.assertRecordValues(
            run.line_ids.sorted(lambda ln: ln.product_id.id),
            [
                {"center_id": center.id, "product_id": x.id, "driver_id": minutes.id, "driver_qty": 2000.0},
                {"center_id": center.id, "product_id": y.id, "driver_id": minutes.id, "driver_qty": 2620.0},
            ],
        )
        run.action_compute()
        quota_x = self._res(run, x).quota_ids
        self.assertAlmostEqual(quota_x.coefficient, 21.6968, places=4)
        self.assertAlmostEqual(quota_x.amount, 43393.69, places=2)
        self.assertAlmostEqual(self._res(run, y).cost_indiretto, 56845.74, places=2)
        with self.assertRaises(UserError):
            run.action_cmc_prefill_lines()
