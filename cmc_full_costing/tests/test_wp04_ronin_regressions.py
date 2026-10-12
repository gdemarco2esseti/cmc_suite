"""WP-04 - regressions from the ronin review (RN-001 .. RN-004), accepted by the orchestrator.

CONTRACT
- RN-001: ``prime_cost_source = 'standard'`` WITH ``produced_measure_id``: ``cost_primo`` is a TOTAL =
  ``standard_price x produced_qty`` (origin ``standard``, primo config ``source = 'standard_price'``,
  ``unit_amount`` = standard_price); the standard prime is all variable; the indirect pool is unchanged
  (ruling 1: stamped direct lines stay in the pool, Σ indirect = 2370). WITHOUT produced measure: v1
  (per-unit ``cost_primo``, ``produced_qty`` 0, unit costs 0, no ``cmc.rate``).
- RN-002: practical basis: ``capacity_used`` and the fixed charge count only the driver lines whose
  ``driver_id`` = the center's ``cmc.capacity.analysis.driver_id``; a costed center without any line on that
  driver (or whose analysis has no driver) -> ValidationError naming the center.
- RN-003: ``actual`` prime cost or a produced measure -> run period == closing period (ValidationError on
  create and write); a standard run without produced measure may stay shorter (v1).
- RN-004: ``action_compute`` calls ``cmc.measure.quantity._cmc_refresh_feeders(date_from, date_to,
  company=<run company>)`` BEFORE reading the produced facts.
Numbers (standard + measure): P1 primo 50 x 100 = 5000, indirect 1362 (Z 1770/100 h x 60 + W 300),
industrial 6362, unit 63.62, variable 5000 + 462 = 5462 (unit 54.62), fixed 900 (9.00); P2 80 x 50 =
4000, indirect 1008, industrial 5008, unit 100.16, variable 4308 (86.16), fixed 700 (14.00). Mark-up 10 %:
P1 69.98, P2 110.18. RN-002: Z 1000 / 125 h x 100 h = 800 charged, idle 200 (kg lines ignored).
"""

from datetime import date
from unittest.mock import patch

from odoo.exceptions import ValidationError
from odoo.tests.common import tagged

from .wp04_common import D_FROM, D_MID, D_TO, Wp04Case

FEED_KEY = "wp04_test_produced_feeder"


@tagged("post_install", "-at_install")
class TestWp04RoninRegressions(Wp04Case):
    def _rates(self, run):
        return self.env["cmc.rate"].search([("origin_model", "=", "cmc.costing.run"), ("origin_id", "=", run.id)])

    # ---- RN-001 -----------------------------------------------------------

    def test_rn001_standard_with_measure_prime_is_total(self):
        # RN-001: with a produced quantity the standard prime cost is standard_price x qty, not one unit
        run = self._run_a(prime_cost_source="standard")
        self.assertRecordValues(
            self._res(run, self.p1) | self._res(run, self.p2),
            [
                {"produced_qty": 100.0, "cost_primo": 5000.0, "prime_cost_origin": "standard", "standard_price_ref": 50.0},
                {"produced_qty": 50.0, "cost_primo": 4000.0, "prime_cost_origin": "standard", "standard_price_ref": 80.0},
            ],
        )
        primo_cfg = self._res(run, self.p1).config_ids.filtered(lambda c: c.level == "primo")
        self.assertRecordValues(primo_cfg, [{"source": "standard_price", "amount": 5000.0, "unit_amount": 50.0}])

    def test_rn001_standard_with_measure_unit_costs_consistent(self):
        # RN-001: unit prime = standard price; the overhead pool is the v1 one (ruling 1)
        run = self._run_a(prime_cost_source="standard")
        self.assertRecordValues(
            self._res(run, self.p1) | self._res(run, self.p2),
            [
                {"cost_indiretto": 1362.0, "cost_industriale": 6362.0, "cost_complessivo": 6362.0,
                 "unit_cost_primo": 50.0, "unit_cost_industriale": 63.62, "unit_cost_complessivo": 63.62,
                 "unit_cost_economico_tecnico": 63.62},
                {"cost_indiretto": 1008.0, "cost_industriale": 5008.0, "cost_complessivo": 5008.0,
                 "unit_cost_primo": 80.0, "unit_cost_industriale": 100.16, "unit_cost_complessivo": 100.16,
                 "unit_cost_economico_tecnico": 100.16},
            ],
        )
        self.assertAlmostEqual(sum(run.result_ids.mapped("cost_indiretto")), 2370.0, places=2)

    def test_rn001_standard_with_measure_variable_fixed_and_price(self):
        # RN-001 / FC-10/11: variable/fixed split and cost-plus price built on the TOTAL standard prime
        run = self._run_a(prime_cost_source="standard", markup_percent=10.0)
        self.assertRecordValues(
            self._res(run, self.p1) | self._res(run, self.p2),
            [
                {"cost_variable": 5462.0, "cost_fixed": 900.0, "unit_cost_variable": 54.62, "unit_cost_fixed": 9.0,
                 "price_cost_plus": 69.98},
                {"cost_variable": 4308.0, "cost_fixed": 700.0, "unit_cost_variable": 86.16, "unit_cost_fixed": 14.0,
                 "price_cost_plus": 110.18},
            ],
        )

    def test_rn001_standard_with_measure_rates(self):
        # RN-001 / AD-108: the published unit costs are the consistent ones
        run = self._run_a(prime_cost_source="standard")
        p1 = {r.component: r.value for r in self._rates(run).filtered(lambda r: r.holder_id == self.obj_p1.id)}
        self.assertAlmostEqual(p1["total"], 63.62, places=2)
        self.assertAlmostEqual(p1["variable"], 54.62, places=2)
        self.assertAlmostEqual(p1["fixed"], 9.00, places=2)
        p2 = self._rates(run).filtered(lambda r: r.holder_id == self.obj_p2.id and r.component == "total")
        self.assertAlmostEqual(p2.value, 100.16, places=2)

    def test_rn001_standard_without_measure_keeps_v1(self):
        # RN-001: no produced measure -> the v1 per-unit standard prime, no unit costs, no rates
        run = self._run_a(prime_cost_source="standard", produced_measure_id=False)
        self.assertRecordValues(
            self._res(run, self.p1) | self._res(run, self.p2),
            [
                {"cost_primo": 50.0, "produced_qty": 0.0, "unit_cost_primo": 0.0, "unit_cost_complessivo": 0.0},
                {"cost_primo": 80.0, "produced_qty": 0.0, "unit_cost_primo": 0.0, "unit_cost_complessivo": 0.0},
            ],
        )
        self.assertFalse(self._rates(run))

    # ---- RN-002 -----------------------------------------------------------

    def _kg_lines(self):
        kg = self.env_a["cmc.cost.driver"].create({"name": "WP04 kg", "code": "WP04KG", "company_id": self.company_a.id})
        return kg, [
            (0, 0, {"center_id": self.z.id, "product_id": self.p1.id, "driver_id": kg.id, "driver_qty": 600}),
            (0, 0, {"center_id": self.z.id, "product_id": self.p2.id, "driver_id": kg.id, "driver_qty": 400}),
        ]

    def test_rn002_practical_counts_only_capacity_driver(self):
        # RN-002: 1000 kg on another measure are not hours of capacity used: charged 800, idle 200
        _kg, kg_lines = self._kg_lines()
        run = self._run_a(capacity_basis="practical", line_ids=[(0, 0, ln) for ln in self._lines_a()] + kg_lines)
        z_row = run.capacity_ids.filtered(lambda c: c.center_id == self.z)
        self.assertRecordValues(
            z_row,
            [{"capacity_practical": 125.0, "capacity_used": 100.0, "fixed_rate": 8.0, "fixed_charged": 800.0,
              "unabsorbed_cost": 200.0}],
        )
        self.assertAlmostEqual(run.unabsorbed_cost, 200.0, places=2)
        indirect = sum(run.result_ids.mapped("cost_indiretto"))
        self.assertAlmostEqual(indirect + run.unabsorbed_cost, 1800.0, places=2)

    def test_rn002_center_without_line_on_capacity_driver_raises(self):
        # RN-002: W's capacity is in kg, its lines are hours -> fail loud, naming the center
        kg, _lines = self._kg_lines()
        self.cap_w.driver_id = kg
        with self.assertRaisesRegex(ValidationError, "WP04 W"):
            self._run_a(capacity_basis="practical")

    def test_rn002_analysis_without_driver_raises(self):
        # RN-002: an analysis without capacity unit cannot tell which lines use it
        self.cap_z.driver_id = False
        with self.assertRaisesRegex(ValidationError, "WP04 Z"):
            self._run_a(capacity_basis="practical")

    # ---- RN-003 -----------------------------------------------------------

    def test_rn003_actual_run_shorter_than_closing_raises(self):
        # RN-003: actual direct lines of half a month against a whole-month closing residual double count
        with self.assertRaises(ValidationError):
            self._run_a(compute=False, produced_measure_id=False, date_to=D_MID)

    def test_rn003_produced_measure_run_shorter_than_closing_raises(self):
        # RN-003: a produced quantity of half a month against a whole-month cost gives a wrong unit cost
        with self.assertRaises(ValidationError):
            self._run_a(compute=False, prime_cost_source="standard", date_from=date(2026, 3, 16))

    def test_rn003_write_shorter_period_raises(self):
        # RN-003: the write path is guarded too
        run = self._run_a(compute=False)
        with self.assertRaises(ValidationError):
            run.write({"date_to": D_MID})

    def test_rn003_standard_v1_shorter_period_allowed(self):
        # RN-003: the v1 standard run without produced measure may still sit inside a longer closing
        run = self._run_a(prime_cost_source="standard", produced_measure_id=False, date_to=D_MID)
        self.assertEqual(run.state, "done")

    # ---- RN-004 -----------------------------------------------------------

    def test_rn004_compute_refreshes_feeders_for_run_period(self):
        # RN-004: the run reads fresh feeder facts of its own period and company
        Fact = type(self.env["cmc.measure.quantity"])
        run = self._run_a(compute=False)
        with patch.object(Fact, "_cmc_refresh_feeders", autospec=True) as refresh:
            run.action_compute()
        refresh.assert_called_once()
        _model, date_from, date_to = refresh.call_args.args
        self.assertEqual((date_from, date_to), (D_FROM, D_TO))
        self.assertEqual(refresh.call_args.kwargs.get("company"), self.company_a)

    def test_rn004_never_refreshed_feeder_counted(self):
        # RN-004: a feeder whose facts were never refreshed still gives the right produced quantity
        self.fact_p2.unlink()
        Fact = type(self.env["cmc.measure.quantity"])

        def feeder(model, date_from, date_to, company=None, keys=None):
            # stand-in for a bridge feeder (cmc_mrp is not a dependency): writes the facts it would load
            model.sudo().create({"driver_id": self.produced.id, "date": date_from, "company_id": company.id,
                                 "center_id": self.z.id, "cost_object_id": self.obj_p2.id, "quantity": 50.0,
                                 "feeder_key": FEED_KEY})

        run = self._run_a(compute=False)
        with patch.object(Fact, "_cmc_refresh_feeders", autospec=True, side_effect=feeder):
            run.action_compute()
        self.assertAlmostEqual(self._res(run, self.p2).produced_qty, 50.0, places=2)
        self.assertAlmostEqual(self._res(run, self.p2).unit_cost_industriale, 18.60, places=2)
