"""WP-04 / FC-04 - actual prime cost from the cost-object-stamped direct lines (AD-106, AD-111).

CONTRACT
- ``cmc.costing.run.prime_cost_source`` Selection ``standard`` (default, v1: ``product.standard_price``) /
  ``actual``. Input field: locked when the run is computed (as ``produced_measure_id``,
  ``capacity_basis``, ``markup_percent``).
- ``actual``: prime cost of a product = cost (``-amount``) of the analytic lines of the run company and
  period with ``cmc_directness = 'direct'``, not excluded, whose ``cmc_cost_object_id`` is the product's
  cost object in the run company (MP + MOD elements). Read with ONE grouped read (AD-111/114).
  ``account.analytic.line.product_id`` (the raw material on MP lines) is never the cost object.
- Those direct lines are booked on the final centers, so they are part of the closing residual: in
  ``actual`` mode the indirect pool spread over the drivers is the residual MINUS the direct cost already
  charged as prime cost (each cost counted once: Σ industrial cost = cost booked on the final centers).
- ``standard_price_ref`` keeps the standard price as reference in both modes; the prime-cost config
  row's ``source`` is ``direct_lines`` in ``actual`` mode (``standard_price`` in ``standard`` mode).
  Standard mode WITH a produced measure (RN-001: total = standard x qty) is in test_wp04_ronin_regressions.
- ``cmc.costing.result.prime_cost_origin`` Selection ``standard`` / ``actual`` / ``standard_fallback``
  (ruling 5): ``standard`` mode -> ``standard`` on every result; ``actual`` mode -> ``actual`` for a
  product with stamped direct lines; a product in scope WITHOUT stamped direct lines falls back to
  ``cost_primo = standard_price x produced_qty`` (0 without produced qty, e.g. the raw material RM) with
  origin ``standard_fallback`` and primo config row ``source = 'standard_fallback'``.
- Standard mode by design (ruling 1): ``cost_primo`` is the per-unit standard reference and the stamped
  direct lines STAY in the overhead pool: Σ ``cost_indiretto`` = Σ final-center residual of the closing
  (Z 1200 + 570 direct = 1770, W 600 -> 2370). Standard x qty + variance is WP-03.
Numbers: see ``wp04_common``. P1 primo 300+120 = 420, P2 150. Z pool 1200 over 100 h (12/h), W 600 over
60 h (10/h): P1 indirect 720+300 = 1020, P2 480+300 = 780; industrial P1 1440, P2 930 (Σ 2370).
"""

from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .wp04_common import Wp04Case


@tagged("post_install", "-at_install")
class TestWp04ActualPrimeCost(Wp04Case):
    def test_fc04_default_source_is_standard(self):
        # FC-04: existing runs keep the v1 behaviour unless the option is chosen
        run = self.env_a["cmc.costing.run"].create({
            "name": "default", "company_id": self.company_a.id, "closing_run_id": self.closing.id,
            "date_from": self.closing.date_from, "date_to": self.closing.date_to,
        })
        self.assertEqual(run.prime_cost_source, "standard")
        self.assertEqual(run.capacity_basis, "actual")

    def test_fc04_actual_prime_cost_from_direct_lines(self):
        # FC-04: prime cost = Σ MP + MOD actually booked on the product, not its standard price
        run = self._run_a()
        self.assertRecordValues(
            self._res(run, self.p1) | self._res(run, self.p2),
            [
                {"product_id": self.p1.id, "cost_primo": 420.0, "standard_price_ref": 50.0},
                {"product_id": self.p2.id, "cost_primo": 150.0, "standard_price_ref": 80.0},
            ],
        )
        primo_cfg = self._res(run, self.p1).config_ids.filtered(lambda c: c.level == "primo")
        self.assertEqual(primo_cfg.source, "direct_lines")
        self.assertEqual(set(run.result_ids.mapped("prime_cost_origin")), {"actual"})

    def test_fc04_direct_cost_not_spread_again_as_overhead(self):
        # FC-04: the direct lines sit in the final-center residual; spreading them again double counts
        run = self._run_a()
        self.assertRecordValues(
            self._res(run, self.p1) | self._res(run, self.p2),
            [
                {"cost_indiretto": 1020.0, "cost_industriale": 1440.0},
                {"cost_indiretto": 780.0, "cost_industriale": 930.0},
            ],
        )
        self.assertAlmostEqual(sum(run.result_ids.mapped("cost_industriale")), 2370.0, places=2)
        z_quota = self._res(run, self.p1).quota_ids.filtered(lambda q: q.center_id == self.z)
        self.assertAlmostEqual(z_quota.coefficient, 12.0, places=4)

    def test_fc04_raw_material_on_line_is_not_cost_object(self):
        # AD-106 inv. 4: product_id of an MP line is the raw material, it receives no prime cost
        run = self._run_a(product_ids=[(6, 0, (self.p1 | self.p2 | self.rm).ids)])
        self.assertRecordValues(
            self._res(run, self.rm),
            [{"cost_primo": 0.0, "cost_industriale": 0.0, "prime_cost_origin": "standard_fallback"}],
        )
        self.assertAlmostEqual(self._res(run, self.p1).cost_primo, 420.0, places=2)
        self.assertEqual(self._res(run, self.p1).prime_cost_origin, "actual")

    def test_fc04_standard_option_keeps_standard_price(self):
        # FC-04: standard_price stays available as the fallback option
        run = self._run_a(prime_cost_source="standard", produced_measure_id=False)
        self.assertRecordValues(
            self._res(run, self.p1) | self._res(run, self.p2),
            [{"cost_primo": 50.0}, {"cost_primo": 80.0}],
        )

    def test_fc04_standard_mode_keeps_direct_lines_in_pool(self):
        # Ruling 1 (characterization): standard mode does NOT subtract the stamped direct lines, the whole
        # final-center residual is spread as overhead and every result is flagged 'standard'
        residual = sum(
            self.closing.line_ids.filtered(lambda l: l.center_type == "final").mapped("cost_residual")
        )
        self.assertAlmostEqual(residual, 2370.0, places=2)  # Z 1200 + 570 direct + W 600
        run = self._run_a(prime_cost_source="standard", produced_measure_id=False)
        self.assertAlmostEqual(sum(run.result_ids.mapped("cost_indiretto")), residual, places=2)
        self.assertRecordValues(
            self._res(run, self.p1) | self._res(run, self.p2),
            [
                # Z 1770 / 100 h = 17.7/h, W 600 / 60 h = 10/h
                {"cost_primo": 50.0, "cost_indiretto": 1362.0, "prime_cost_origin": "standard"},
                {"cost_primo": 80.0, "cost_indiretto": 1008.0, "prime_cost_origin": "standard"},
            ],
        )
        primo_cfg = self._res(run, self.p1).config_ids.filtered(lambda c: c.level == "primo")
        self.assertEqual(primo_cfg.source, "standard_price")

    def test_fc04_actual_fallback_standard_price_times_produced(self):
        # Ruling 5: actual mode, product in scope with produced facts but no stamped direct lines ->
        # cost_primo = standard_price x produced_qty, flagged standard_fallback (not silently 0)
        p3 = self.env["product.product"].create({"name": "WP04 P3"})
        p3.with_company(self.company_a).standard_price = 30
        obj_p3 = self.env_a["cmc.cost.object"]._cmc_ensure_objects(p3, company=self.company_a)
        self._fact(self.env_a, self.produced, obj_p3, 20, center=self.z)
        run = self._run_a(product_ids=[(6, 0, (self.p1 | self.p2 | p3).ids)])
        self.assertRecordValues(
            self._res(run, p3),
            [{"produced_qty": 20.0, "cost_primo": 600.0, "standard_price_ref": 30.0,
              "prime_cost_origin": "standard_fallback"}],
        )
        primo_cfg = self._res(run, p3).config_ids.filtered(lambda c: c.level == "primo")
        self.assertEqual(primo_cfg.source, "standard_fallback")
        # the products with stamped lines are untouched by the fallback
        self.assertRecordValues(
            self._res(run, self.p1) | self._res(run, self.p2),
            [{"cost_primo": 420.0, "prime_cost_origin": "actual"},
             {"cost_primo": 150.0, "prime_cost_origin": "actual"}],
        )

    def test_fc04_options_locked_when_computed(self):
        # AD-001: the new options are inputs of a computed (certified) run
        run = self._run_a()
        for vals in (
            {"prime_cost_source": "standard"},
            {"produced_measure_id": False},
            {"capacity_basis": "practical"},
            {"markup_percent": 10.0},
        ):
            with self.subTest(vals=vals), self.assertRaises(UserError):
                run.write(vals)

    def test_fc04_multi_company_reads_own_cost_objects(self):
        # FC-04 multi-company: company B reads its own stamped lines, company A never sees them
        run_b = self._run_b()
        self.assertRecordValues(
            run_b.result_ids,
            [{"product_id": self.p1.id, "cost_primo": 777.0, "cost_indiretto": 100.0, "cost_industriale": 877.0}],
        )
        run_a = self._run_a()
        self.assertAlmostEqual(self._res(run_a, self.p1).cost_primo, 420.0, places=2)
