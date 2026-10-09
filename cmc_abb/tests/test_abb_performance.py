"""WP-12 / AD-114 - access cost of ABB and ABC-variance Compute does not grow with cost objects or facts.

CONTRACT
- ABB Compute reads the planned facts (unbounded: objects x months x versions) with ONE grouped read by cost
  object, the calibration results grouped, walks activities / resources (bounded master data) in Python and
  creates results, budget lines (one ``_cmc_budget_add`` batch per resource, not per object) and rates in
  batches: the query count for 30 products x 12 monthly facts equals the one for 3 products (+ tolerance).
- Variance Compute reads actual facts grouped by driver and the ledger grouped by center x element: the
  query count is independent of the number of fact rows and ledger lines.
"""

from datetime import date

from odoo.tests.common import tagged

from .common import AbbCase

TOLERANCE = 5


@tagged("post_install", "-at_install")
class TestAbbPerformance(AbbCase):
    def _volume_run(self, n):
        products = self.env["product.product"].create([{"name": "WP12 vol %s-%s" % (n, i)} for i in range(n)])
        objects = self.env_a["cmc.cost.object"]._cmc_ensure_objects(products, company=self.company_a)
        version = self._version(self.env_a, "WP12 FC vol %s" % n, "forecast")
        self.env_a["cmc.measure.quantity"].create([
            {"driver_id": self.units.id, "date": date(2027, month, 1), "company_id": self.company_a.id,
             "cost_object_id": obj.id, "quantity": 10.0, "version_id": version.id}
            for obj in objects for month in range(1, 13)
        ])
        version.with_user(self.manager_a).action_approve()
        target = self._version(self.env_a, "WP12 BUD vol %s" % n, "budget")
        bill = [{"cost_object_id": obj.id, "activity_id": act.id, "qty_per_unit": qty}
                for obj in objects for act, qty in ((self.mach, 0.5), (self.setup, 0.01), (self.ship, 0.4))]
        return self._abb_run(compute=False, source_version_id=version.id, target_version_id=target.id, bill=bill)

    def _query_budget(self, small_records, action):
        self.env.flush_all()
        self.env.invalidate_all()
        start = self.cr.sql_log_count
        action(small_records)
        self.env.flush_all()
        self.env.invalidate_all()
        return self.cr.sql_log_count - start + TOLERANCE

    def test_abb_compute_query_count_independent_of_objects(self):
        # AD-114: a real forecast has thousands of products x 12 months; 30 must cost the queries of 3
        small, big = self._volume_run(3), self._volume_run(30)
        budget = self._query_budget(small, lambda run: run.action_compute())
        with self.assertQueryCount(budget):
            big.action_compute()
            self.env.flush_all()
        # 30 x 120 units: MACH 1,800 h, SETUP 36, SHIP 1,440
        self.assertAlmostEqual(self._act(big, self.mach).demand_qty, 1800.0, places=4)
        self.assertEqual(len(big.object_result_ids), 30)
        self.assertEqual(len(self.env["cmc.budget.line"].search([("abb_run_id", "=", big.id)])), 48)

    def test_variance_compute_query_count_independent_of_facts(self):
        # AD-114: actual facts and ledger lines grow every day; the variance reads them grouped
        abb = self._abb_run(target_version_id=False)
        objects = self.env_a["cmc.cost.object"]._cmc_ensure_objects(
            self.env["product.product"].create([{"name": "WP12 var %s" % i} for i in range(40)]), company=self.company_a
        )

        def load(n, month):
            day = date(2027, month, 10)
            self.env_a["cmc.measure.quantity"].create([
                {"driver_id": self.mhours.id, "date": day, "company_id": self.company_a.id,
                 "cost_object_id": obj.id, "quantity": 5.0} for obj in objects[:n]
            ])
            self.env_a["account.analytic.line"].create([
                {"name": "wp12 vol", "account_id": self.z.id, "amount": -10.0, "company_id": self.company_a.id,
                 "cmc_cost_element_id": self.sal.id, "cmc_behavior": "fixed", "date": day} for _i in range(n)
            ])
            return self.env_a["cmc.abb.variance.run"].create({
                "name": "WP12 vol %s" % n, "company_id": self.company_a.id, "abb_run_id": abb.id,
                "date_from": date(2027, month, 1), "date_to": date(2027, month, 28),
            })

        small, big = load(3, 1), load(40, 2)
        budget = self._query_budget(small, lambda run: run.action_compute())
        with self.assertQueryCount(budget):
            big.action_compute()
            self.env.flush_all()
        tech = big.line_ids.filtered(lambda ln: ln.resource_id == self.tech)
        self.assertAlmostEqual(tech.actual_demand_qty, 200.0, places=4)  # 40 x 5 h x 1 technician h per machine h
        self.assertAlmostEqual(tech.actual_cost, 400.0, places=2)
