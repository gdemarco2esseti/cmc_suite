"""WP-08 / AD-114 - access cost of the ABM layer does not grow with cost objects, flows or measure facts.

CONTRACT
- Compute reads the activity measure facts (unbounded: one per day, per activity, per measure) and the defect
  facts (one per cost object) with grouped reads, and creates performance rows / snapshots / Pareto fields in
  batches. The ABM helpers (``_cmc_object_cost_by_attribute``, ``_cmc_activity_pareto``, ``_cmc_coq_split``,
  ``_cmc_activity_cost_quadrant``, ``activity._cmc_performance_trend``) read the stored results grouped, never
  per object / per flow / per fact. Same query count for 3 or 30 products (x 30 daily facts) within a small
  constant tolerance; a per-object or per-fact query = N+1 = red.
"""

from datetime import timedelta

from odoo.tests.common import tagged

from .abm_common import AbmCase
from .abc2_common import D_FROM, D_TO

TOLERANCE = 5


@tagged("post_install", "-at_install")
class TestAbmPerformance(AbmCase):
    def _volume(self, n):
        company = self.env["res.company"].create({"name": "CMC WP08 volume %s" % n})
        env = self._company_env(company)
        sal = env["cmc.cost.element"].create({"name": "vol sal", "code": "WP08VSAL", "company_id": company.id})
        z = self._center(env, "WP08 vol Z %s" % n, company)
        hours = self._driver(env, "WP08VH", "duration", level="unit", company=company)
        setups = self._driver(env, "WP08VS", "transaction", level="batch", company=company)
        units = self._driver(env, "WP08VU", "transaction", kind="statistical", company=company)
        defects = self._driver(env, "WP08VD", "transaction", kind="statistical", company=company)
        out = self._driver(env, "WP08VO", "transaction", kind="statistical", company=company)
        cyc = self._driver(env, "WP08VC", "duration", kind="statistical", company=company)
        Act = env["cmc.abc.activity"]
        process = Act.create({"name": "vol process", "code": "WP08VP", "node_type": "process"})
        mach = self._activity("vol machines", "WP08VM", process, "unit", hours, env=env)
        setup = self._activity("vol setups", "WP08VSU", process, "batch", setups, env=env)
        mach.cmc_attribute_value_ids = [(6, 0, (self.va | self.coq_free | self.e4).ids)]
        setup.cmc_attribute_value_ids = [(6, 0, (self.nva | self.coq_prev | self.e2).ids)]
        env["cmc.abc.activity.measure"].create([
            {"activity_id": act.id, "measure_id": measure.id, "role": role}
            for act in (mach, setup) for measure, role in ((out, "output"), (cyc, "cycle_time"), (defects, "quality"))
        ])
        resource = env["cmc.abc.resource"].create(
            {"name": "vol people", "code": "WP08VR", "center_id": z.id, "cost_element_ids": [(6, 0, sal.ids)]}
        )
        products = self.env["product.product"].create([{"name": "WP08 vol %s-%s" % (n, i)} for i in range(n)])
        objects = env["cmc.cost.object"]._cmc_ensure_objects(products, company=company)
        common = {"account_id": z.id, "company_id": company.id, "date": D_FROM, "cmc_cost_element_id": sal.id}
        env["account.analytic.line"].create(
            [{"name": "pool f", "amount": -100.0, "cmc_behavior": "fixed", **common} for _o in objects]
            + [{"name": "trace", "amount": -10.0, "cmc_behavior": "variable", "cmc_directness": "direct",
                "cmc_cost_object_id": o.id, **common} for o in objects]
        )
        facts = [{"driver_id": units.id, "date": D_FROM, "company_id": company.id, "cost_object_id": o.id, "quantity": 10.0}
                 for o in objects]
        facts += [{"driver_id": defects.id, "date": D_FROM, "company_id": company.id, "cost_object_id": o.id, "quantity": 1.0}
                  for o in objects]
        days = [D_FROM + timedelta(days=d) for d in range(min(n, 30))]
        facts += [{"driver_id": measure.id, "activity_id": act.id, "date": day, "company_id": company.id, "quantity": 1.0}
                  for act in (mach, setup) for measure in (out, cyc, defects) for day in days]
        env["cmc.measure.quantity"].create(facts)
        edges = [(0, 0, {"activity_id": mach.id, "cost_object_id": o.id, "driver_id": hours.id, "driver_qty": 2}) for o in objects]
        edges += [(0, 0, {"activity_id": setup.id, "cost_object_id": o.id, "driver_id": setups.id, "driver_qty": 1}) for o in objects]
        run = env["cmc.abc.run"].create({
            "name": "WP08 volume %s" % n, "company_id": company.id, "date_from": D_FROM, "date_to": D_TO,
            "trace_direct": True, "output_measure_id": units.id, "defect_measure_id": defects.id,
            "resource_line_ids": [(0, 0, {"resource_id": resource.id, "activity_id": mach.id, "percent": 60}),
                                  (0, 0, {"resource_id": resource.id, "activity_id": setup.id, "percent": 40})],
            "line_ids": edges,
        })
        return run, mach

    def _exercise(self, run, activity):
        run.action_compute()
        self.env.flush_all()
        run._cmc_object_cost_by_attribute("VA_CLASS")
        run._cmc_activity_pareto()
        run._cmc_coq_split()
        run._cmc_activity_cost_quadrant("VA_CLASS", "EFFICIENCY")
        activity._cmc_performance_trend(D_FROM, D_TO)

    def test_abm_query_count_independent_of_objects_and_facts(self):
        # AD-114: 30 products x 30 daily facts cost the same queries as 3 x 3
        small, small_act = self._volume(3)
        big, big_act = self._volume(30)
        self.env.flush_all()
        self.env.invalidate_all()
        start = self.cr.sql_log_count
        self._exercise(small, small_act)
        budget = self.cr.sql_log_count - start + TOLERANCE
        self.env.invalidate_all()
        with self.assertQueryCount(budget):
            self._exercise(big, big_act)
        # pool 30 x 100 = 3000: MACH 1800, 30 outputs (one per day) -> 60 per output; NVA = SETUP 1200 / 30 = 40 per object
        perf = big.performance_ids.filtered(lambda p: p.activity_id == big_act)
        self.assertAlmostEqual(perf.cost_per_output, 60.0, places=2)
        self.assertAlmostEqual(big.result_ids[:1].nva_cost, 40.0, places=2)
        self.assertEqual(len(big.result_ids), 30)
