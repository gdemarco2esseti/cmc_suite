"""WP-04 / AD-114 - access cost of Compute does not grow with the number of products (volume fixture).

CONTRACT
- Compute of an ``actual`` run with ``produced_measure_id`` and ``capacity_basis = 'practical'`` reads the
  direct lines (``account.analytic.line``, unbounded) and the measure facts (unbounded) with grouped
  reads at the cost-object grain, resolves cost objects in one batch, creates results/quotas/configs/
  capacity rows and publishes the rates in batches: its query count is the same for 3 or 40 products
  (a small constant tolerance for chunking only). A per-product search/browse = N+1 = red.
"""

from odoo.tests.common import tagged

from .wp04_common import D_FROM, D_TO, Wp04Case

TOLERANCE = 4


@tagged("post_install", "-at_install")
class TestWp04Performance(Wp04Case):
    def _volume_run(self, n):
        company = self.env["res.company"].create({"name": "CMC WP04 volume %s" % n})
        env = self._company_env(company)
        hours = env["cmc.cost.driver"].create({"name": "H vol", "code": "WP04HV", "company_id": company.id})
        produced = self._measure(env, company, "WP04PQV")
        z = self._center(env, "WP04 vol Z %s" % n, company)
        w = self._center(env, "WP04 vol W %s" % n, company)
        products = self.env["product.product"].create([{"name": "WP04 vol %s-%s" % (n, i)} for i in range(n)])
        objects = env["cmc.cost.object"]._cmc_ensure_objects(products, company=company)
        AL = env["account.analytic.line"]
        common = {"account_id": z.id, "company_id": company.id, "date": D_FROM, "cmc_directness": "direct",
                  "cmc_behavior": "variable"}
        AL.create(
            [{"name": "mp", "amount": -10.0, "cmc_cost_element_id": self.mp.id, "cmc_cost_object_id": o.id, **common}
             for o in objects]
            + [{"name": "mod", "amount": -5.0, "cmc_cost_element_id": self.mod.id, "cmc_cost_object_id": o.id, **common}
               for o in objects]
        )
        self._indirect(env, z, 1000, "fixed")
        self._indirect(env, z, 200, "variable")
        self._indirect(env, w, 600, "fixed")
        closing = self._closing(env, company)
        env["cmc.measure.quantity"].create([
            {"driver_id": produced.id, "date": D_FROM, "company_id": company.id, "center_id": z.id,
             "cost_object_id": o.id, "quantity": 10.0}
            for o in objects
        ])
        self._capacity(env, z, company, theoretical=4 * n, practical=3 * n, actual=2 * n)
        self._capacity(env, w, company, theoretical=n, practical=n, actual=n)
        lines = [(0, 0, {"center_id": z.id, "product_id": p.id, "driver_id": hours.id, "driver_qty": 2}) for p in products]
        lines += [(0, 0, {"center_id": w.id, "product_id": p.id, "driver_id": hours.id, "driver_qty": 1}) for p in products]
        return env["cmc.costing.run"].create({
            "name": "WP04 volume %s" % n, "company_id": company.id, "closing_run_id": closing.id,
            "date_from": D_FROM, "date_to": D_TO, "base": "multiple", "product_ids": [(6, 0, products.ids)],
            "line_ids": lines, "prime_cost_source": "actual", "produced_measure_id": produced.id,
            "capacity_basis": "practical", "markup_percent": 10.0,
        })

    def test_compute_query_count_independent_of_products(self):
        # AD-114: 40 products must cost the same queries as 3 (the list of a real company has thousands)
        small = self._volume_run(3)
        big = self._volume_run(40)
        self.env.flush_all()
        self.env.invalidate_all()
        start = self.cr.sql_log_count
        small.action_compute()
        self.env.flush_all()
        budget = self.cr.sql_log_count - start + TOLERANCE
        self.env.invalidate_all()
        with self.assertQueryCount(budget):
            big.action_compute()
        # the volume fixture is also correct, not only fast: 15 prime + full absorption of the used capacity
        res = big.result_ids[:1]
        self.assertAlmostEqual(res.cost_primo, 15.0, places=2)
        self.assertAlmostEqual(res.produced_qty, 10.0, places=2)
        self.assertEqual(len(big.result_ids), 40)
