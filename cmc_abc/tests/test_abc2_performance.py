"""WP-07 / AD-114 - access cost of the ABC v2 Compute does not grow with objects, edges or ledger lines.

CONTRACT
- Compute reads the analytic lines (unbounded) with grouped reads at center x element x behavior (pools),
  activity tag and cost-object (trace) grain, the measure facts (unbounded) grouped by cost object,
  resolves cost objects in one batch, walks the network over bounded master data in Python (AD-114 (6)),
  and creates activity costs / flows / results / rates in batches: the query count is the same for 3 or
  30 products (each with ledger lines, a traced line, two activity edges and output facts), within a small
  constant tolerance. A per-object or per-edge search/browse/create = N+1 = red.
"""

from odoo.tests.common import tagged

from .abc2_common import D_FROM, D_TO, Abc2Case

TOLERANCE = 5


@tagged("post_install", "-at_install")
class TestAbc2Performance(Abc2Case):
    def _volume_run(self, n):
        company = self.env["res.company"].create({"name": "CMC WP07 volume %s" % n})
        env = self._company_env(company)
        sal = env["cmc.cost.element"].create({"name": "vol sal", "code": "WP07VSAL", "company_id": company.id})
        z = self._center(env, "WP07 vol Z %s" % n, company)
        hours = self._driver(env, "WP07VH", "duration", level="unit", company=company)
        setups = self._driver(env, "WP07VS", "transaction", level="batch", company=company)
        units = self._driver(env, "WP07VU", "transaction", kind="statistical", company=company)
        Act = env["cmc.abc.activity"]
        process = Act.create({"name": "vol process", "code": "WP07VP", "node_type": "process"})
        mach = self._activity("vol machines", "WP07VM", process, "unit", hours, env=env)
        setup = self._activity("vol setups", "WP07VSU", process, "batch", setups, env=env)
        resource = env["cmc.abc.resource"].create(
            {"name": "vol people", "code": "WP07VR", "center_id": z.id, "cost_element_ids": [(6, 0, sal.ids)]}
        )
        products = self.env["product.product"].create([{"name": "WP07 vol %s-%s" % (n, i)} for i in range(n)])
        objects = env["cmc.cost.object"]._cmc_ensure_objects(products, company=company)
        common = {"account_id": z.id, "company_id": company.id, "date": D_FROM, "cmc_cost_element_id": sal.id}
        env["account.analytic.line"].create(
            [{"name": "pool f", "amount": -100.0, "cmc_behavior": "fixed", **common} for _o in objects]
            + [{"name": "pool v", "amount": -50.0, "cmc_behavior": "variable", **common} for _o in objects]
            + [{"name": "trace", "amount": -10.0, "cmc_behavior": "variable", "cmc_directness": "direct",
                "cmc_cost_object_id": o.id, **common} for o in objects]
        )
        env["cmc.measure.quantity"].create([
            {"driver_id": units.id, "date": D_FROM, "company_id": company.id, "cost_object_id": o.id, "quantity": 10.0}
            for o in objects
        ])
        edges = [(0, 0, {"activity_id": mach.id, "cost_object_id": o.id, "driver_id": hours.id, "driver_qty": 2})
                 for o in objects]
        edges += [(0, 0, {"activity_id": setup.id, "cost_object_id": o.id, "driver_id": setups.id, "driver_qty": 1})
                  for o in objects]
        return env["cmc.abc.run"].create({
            "name": "WP07 volume %s" % n, "company_id": company.id, "date_from": D_FROM, "date_to": D_TO,
            "trace_direct": True, "output_measure_id": units.id,
            "resource_line_ids": [(0, 0, {"resource_id": resource.id, "activity_id": mach.id, "percent": 60}),
                                  (0, 0, {"resource_id": resource.id, "activity_id": setup.id, "percent": 40})],
            "capacity_line_ids": [(0, 0, {"activity_id": mach.id, "practical_capacity": 4 * n})],
            "line_ids": edges,
        })

    def test_compute_query_count_independent_of_objects(self):
        # AD-114: 30 products must cost the same queries as 3 (a real model has thousands of objects)
        small = self._volume_run(3)
        big = self._volume_run(30)
        self.env.flush_all()
        self.env.invalidate_all()
        start = self.cr.sql_log_count
        small.action_compute()
        self.env.flush_all()
        budget = self.cr.sql_log_count - start + TOLERANCE
        self.env.invalidate_all()
        with self.assertQueryCount(budget):
            big.action_compute()
            self.env.flush_all()
        res = big.result_ids.filtered(lambda r: r.cost_object_id)[:1]
        # pool 30 x 150 = 4500: machines 2700 (F 1800 / 120 h = 15, V 900 / 60 h = 15) -> 2 h x 30 = 60;
        # setups 1800 / 30 = 60; traced 10 -> 130 per object; unused (120 - 60) x 15 = 900
        self.assertAlmostEqual(res.amount, 130.0, places=2)
        self.assertEqual(len(big.result_ids), 30)
        self.assertAlmostEqual(big.unused_capacity_cost, 900.0, places=2)
