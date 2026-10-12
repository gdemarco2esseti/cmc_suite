"""WP-07 / ABC-04 (rates published, AD-108), run lifecycle (AD-001/AD-113), v1 compatibility (AD-101).

CONTRACT (see abc2_common for the whole WP)
- Compute with ``date_from`` publishes ``cmc.rate`` rows (origin = run, month of date_from):
  holder ``cmc.abc.activity`` x driver = activity ``driver_id`` x component total / fixed / variable;
  holder product ``cmc.cost.object`` x driver = ``output_measure_id`` x unit cost total / fixed / variable.
  No ``date_from`` -> nothing published. Reset / unlink of the run removes them (mixin).
- New inputs (``resource_line_ids``, ``capacity_line_ids``, the new header fields) are locked when the run
  is computed (UserError); new results (``activity_cost_ids``, ``flow_ids``) are result-mixin rows: never
  written by hand, deleted by reset; recompute after reset is idempotent.
- AD-101: a v1-shaped run (tag pools + activity/product lines, no resources) gives the v1 figures and the
  v1 surface (``result_ids.product_id``, ``activity_result_ids``, ``unallocated_amount``, ``_cmc_reclass``);
  every v1 test in test_abc.py / test_reclass.py stays valid.
"""

from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .abc2_common import D_FROM, D_MID, D_TO, Abc2Case


@tagged("post_install", "-at_install")
class TestAbc2RatesLifecycle(Abc2Case):
    def _rate(self, run, holder, driver, component="total"):
        return self.env["cmc.rate"]._cmc_lookup(holder, driver, D_MID, origin=run, component=component,
                                                company=self.company_a)

    def test_abc04_activity_rates_published(self):
        # ABC-04 / AD-108: WP-09/10/12 read activity rates from the registry, not from cmc_abc
        run = self._main_run()
        self.assertAlmostEqual(self._rate(run, self.mach, self.mhours).value, 62.8, places=4)
        self.assertAlmostEqual(self._rate(run, self.mach, self.mhours, "fixed").value, 42.8, places=4)
        self.assertAlmostEqual(self._rate(run, self.mach, self.mhours, "variable").value, 20.0, places=4)
        self.assertAlmostEqual(self._rate(run, self.setup, self.setups).value, 365.0, places=4)
        self.assertAlmostEqual(self._rate(run, self.admin, self.tickets).value, 250.0, places=4)

    def test_abc04_unit_costs_published(self):
        # ABC-04 / AD-108: ABC unit cost per product (P1 77.58 = F 50.58 + V 27)
        run = self._main_run()
        self.assertAlmostEqual(self._rate(run, self.obj_p1, self.produced).value, 77.58, places=2)
        self.assertAlmostEqual(self._rate(run, self.obj_p1, self.produced, "fixed").value, 50.58, places=2)
        self.assertAlmostEqual(self._rate(run, self.obj_p1, self.produced, "variable").value, 27.0, places=2)

    def test_rates_removed_on_reset(self):
        # AD-108: published rates are result rows of the run
        run = self._main_run()
        run.action_reset_to_draft()
        self.assertFalse(self.env["cmc.rate"].search([("origin_model", "=", "cmc.abc.run"), ("origin_id", "=", run.id)]))

    def test_no_rates_without_period(self):
        # AD-108: a rate is valid for a month; a run without dates has none to publish
        run = self._main_run(date_from=False, date_to=False)
        self.assertEqual(run.state, "done")
        self.assertFalse(self.env["cmc.rate"].search([("origin_model", "=", "cmc.abc.run"), ("origin_id", "=", run.id)]))

    def test_computed_run_locks_new_inputs(self):
        # AD-001: resource matrix, capacity and options are inputs of the certified figures
        run = self._main_run()
        with self.assertRaises(UserError):
            run.resource_line_ids[0].percent = 40
        with self.assertRaises(UserError):
            self.env_a["cmc.abc.capacity"].create({"run_id": run.id, "activity_id": self.qc.id, "practical_capacity": 9})
        with self.assertRaises(UserError):
            run.trace_direct = False

    def test_results_written_by_compute_only(self):
        # AD-001: a flow typed by hand would be certified next to the engine's rows
        run = self._main_run()
        with self.assertRaises(UserError):
            self.env_a["cmc.abc.flow"].create({"run_id": run.id, "stage": "trace", "amount": 1.0})
        with self.assertRaises(UserError):
            run.activity_cost_ids[0].total_cost = 1.0

    def test_reset_and_recompute_idempotent(self):
        # AD-113: reset deletes every result, recompute rebuilds the same figures
        run = self._main_run()
        counts = (len(run.activity_cost_ids), len(run.flow_ids), len(run.result_ids))
        run.action_reset_to_draft()
        self.assertFalse(run.activity_cost_ids or run.flow_ids or run.result_ids)
        run.action_compute()
        self.assertEqual((len(run.activity_cost_ids), len(run.flow_ids), len(run.result_ids)), counts)
        self.assertAlmostEqual(self._res(run, self.obj_p1).amount, 7758.0, places=2)

    def test_v1_shaped_run_reproduces_v1(self):
        # AD-101: tag pool QC 500 split 60/40 by a v1 activity/product line -> 300/200, v1 surface intact
        run = self.env_a["cmc.abc.run"].create({
            "name": "WP07 v1 shaped", "company_id": self.company_a.id, "date_from": D_FROM, "date_to": D_TO,
            "line_ids": [
                (0, 0, {"activity_id": self.qc.id, "product_id": self.p1.id, "driver_qty": 60}),
                (0, 0, {"activity_id": self.qc.id, "product_id": self.p2.id, "driver_qty": 40}),
            ],
        })
        run.action_compute()
        p1 = run.result_ids.filtered(lambda r: r.product_id == self.p1)
        self.assertAlmostEqual(p1.amount, 300.0, places=2)
        self.assertEqual(p1.cost_object_id, self.obj_p1)
        self.assertAlmostEqual(sum(run.result_ids.mapped("amount")), 500.0, places=2)
        self.assertFalse(run.unallocated_amount)
        self.assertEqual(set(run.activity_result_ids.mapped("activity_id")), {self.qc})
        self.assertAlmostEqual(self._act(run, self.qc).resource_cost, 500.0, places=2)
        subtotal = next(r for r in run._cmc_reclass()["rows"] if r["kind"] == "subtotal")
        self.assertAlmostEqual(subtotal["total"], 500.0, places=2)
