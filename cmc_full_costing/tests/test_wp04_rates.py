"""WP-04 / AD-108 + AD-002 - unit costs published to ``cmc.rate``; the closing recorded as run usage.

CONTRACT
- On Compute, for every result with ``produced_qty > 0`` and a ``produced_measure_id``, the run publishes
  (``cmc.rate._cmc_publish``, under ``CMC_RUN_STATE``) three rows: holder = the product's
  ``cmc.cost.object`` in the run company, ``driver_id`` = produced measure, ``date`` = first day of the
  month of the run (single-month runs here), components ``total`` = ``unit_cost_complessivo``,
  ``variable`` = ``unit_cost_variable``, ``fixed`` = ``unit_cost_fixed``; origin = the costing run,
  company = run company. No produced quantity -> no rate for that product.
- Reset to draft (and unlink) removes them (cmc.run.mixin ``_cmc_release_published``); a new Compute
  publishes again.
- On Compute the run records ``cmc.run.usage`` (consumer ``cmc.costing.run``, origin ``cmc.closing.run``)
  via ``_cmc_record_usage``; reset removes it; the closing cannot be reset while the costing is computed.
Numbers: P1 total 14.40 / variable 5.40 / fixed 9.00; company B P1 total 87.70 (877 / 10).
"""

from datetime import date

from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .wp04_common import D_MID, Wp04Case


@tagged("post_install", "-at_install")
class TestWp04Rates(Wp04Case):
    def _rates(self, run):
        return self.env["cmc.rate"].search(
            [("origin_model", "=", "cmc.costing.run"), ("origin_id", "=", run.id)], order="holder_id, component"
        )

    def test_publish_unit_costs_on_compute(self):
        # AD-108 / FC-05: the computed unit cost becomes a rate consumers can read without depending on us
        run = self._run_a()
        rates = self._rates(run).filtered(lambda r: r.holder_id == self.obj_p1.id)
        self.assertEqual(len(rates), 3)
        by_component = {r.component: r for r in rates}
        self.assertAlmostEqual(by_component["total"].value, 14.40, places=2)
        self.assertAlmostEqual(by_component["variable"].value, 5.40, places=2)
        self.assertAlmostEqual(by_component["fixed"].value, 9.00, places=2)
        self.assertRecordValues(
            by_component["total"],
            [{"holder_model": "cmc.cost.object", "driver_id": self.produced.id, "date": date(2026, 3, 1),
              "company_id": self.company_a.id}],
        )
        found = self.env["cmc.rate"]._cmc_lookup(self.obj_p1, self.produced, D_MID, company=self.company_a)
        self.assertAlmostEqual(found.value, 14.40, places=2)
        p2_total = self._rates(run).filtered(lambda r: r.holder_id == self.obj_p2.id and r.component == "total")
        self.assertAlmostEqual(p2_total.value, 18.60, places=2)

    def test_no_rate_without_produced_qty(self):
        self.fact_p2.unlink()
        run = self._run_a()
        self.assertFalse(self._rates(run).filtered(lambda r: r.holder_id == self.obj_p2.id))
        self.assertEqual(len(self._rates(run)), 3)

    def test_reset_removes_rates_and_compute_republishes(self):
        # AD-001/108: published rates are result rows of the run
        run = self._run_a()
        self.assertTrue(self._rates(run))
        run.action_reset_to_draft()
        self.assertFalse(self._rates(run))
        run.action_compute()
        self.assertEqual(len(self._rates(run)), 6)

    def test_unlink_removes_rates(self):
        run = self._run_a()
        run_id = run.id
        self.assertTrue(self._rates(run))
        run.unlink()
        self.assertFalse(self.env["cmc.rate"].search_count([("origin_model", "=", "cmc.costing.run"), ("origin_id", "=", run_id)]))

    def test_closing_recorded_as_usage(self):
        # AD-002 generic register: the costing reads the closing
        run = self._run_a()
        Usage = self.env["cmc.run.usage"]
        domain = [("consumer_model", "=", "cmc.costing.run"), ("consumer_id", "=", run.id),
                  ("origin_model", "=", "cmc.closing.run"), ("origin_id", "=", self.closing.id)]
        self.assertEqual(Usage.search_count(domain), 1)
        self.assertEqual(Usage.search(domain).company_id, self.company_a)
        with self.assertRaises(UserError):
            self.closing.action_reset_to_draft()
        run.action_reset_to_draft()
        self.assertEqual(Usage.search_count(domain), 0)

    def test_company_b_rates_scoped(self):
        # multi-company: each company publishes on its own cost object
        run_b = self._run_b()
        rate = self._rates(run_b).filtered(lambda r: r.component == "total")
        self.assertRecordValues(rate, [{"holder_id": self.obj_p1_b.id, "company_id": self.company_b.id}])
        self.assertAlmostEqual(rate.value, 87.70, places=2)
        self.assertFalse(self.env["cmc.rate"]._cmc_lookup(self.obj_p1, self.produced, D_MID, company=self.company_a))
