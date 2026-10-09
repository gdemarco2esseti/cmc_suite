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
  row's ``source`` is not ``standard_price`` in ``actual`` mode.
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
        self.assertNotEqual(primo_cfg.source, "standard_price")

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
        self.assertRecordValues(self._res(run, self.rm), [{"cost_primo": 0.0, "cost_industriale": 0.0}])
        self.assertAlmostEqual(self._res(run, self.p1).cost_primo, 420.0, places=2)

    def test_fc04_standard_option_keeps_standard_price(self):
        # FC-04: standard_price stays available as the fallback option
        run = self._run_a(prime_cost_source="standard")
        self.assertRecordValues(
            self._res(run, self.p1) | self._res(run, self.p2),
            [{"cost_primo": 50.0}, {"cost_primo": 80.0}],
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
