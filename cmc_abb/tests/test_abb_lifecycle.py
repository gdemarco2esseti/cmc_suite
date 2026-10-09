"""WP-12 - run lifecycle of ``cmc.abb.run`` (AD-001, AD-002, AD-104, AD-113) and the fail-loud input checks.

CONTRACT (see common.py for the whole WP)
- Compute: UserError when the source version is not approved, the target is not draft, target == source,
  no planned facts of the output measure, an activity with demand and no usage line, a used resource without
  ``driver_id``, a budgeted resource without element (``budget_element_id`` / single ``cost_element_ids``).
- Inputs locked when computed; results written by Compute only; reset deletes results, the run's budget
  lines and its target-version rates; recompute is idempotent.
- AD-002: usages on the source version and the calibration ABC run (they cannot be reset under it); a
  target approved after Compute locks the run's lines: reset -> UserError, lines intact.
"""

from datetime import date

from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .common import AbbCase


@tagged("post_install", "-at_install")
class TestAbbLifecycle(AbbCase):
    def _target_rates(self):
        return self.env["cmc.rate"].search([("origin_model", "=", "cmc.plan.version"), ("origin_id", "=", self.bud27.id)])

    def test_source_version_must_be_approved(self):
        # AD-104: a budget is built on an approved forecast, not on figures still moving
        draft = self._version(self.env_a, "WP12 FC27 draft", "forecast")
        self._fact(self.env_a, self.units, 100, cost_object=self.obj_p1, day=date(2027, 1, 15), version=draft)
        run = self._abb_run(compute=False, source_version_id=draft.id)
        with self.assertRaises(UserError):
            run.action_compute()

    def test_target_version_must_be_draft_and_distinct(self):
        # AD-104: lines go into an open version; the forecast it reads is not its own target
        approved = self._version(self.env_a, "WP12 BUD27 approved", "budget")
        approved.with_user(self.manager_a).action_approve()
        with self.assertRaises(UserError):
            self._abb_run(target_version_id=approved.id)
        with self.assertRaises(UserError):
            self._abb_run(target_version_id=self.fc27.id)

    def test_no_planned_volume_fails_loud(self):
        # BUD-06: an empty forecast would silently produce a zero budget
        empty = self._version(self.env_a, "WP12 FC27 empty", "forecast")
        empty.with_user(self.manager_a).action_approve()
        with self.assertRaises(UserError):
            self._abb_run(source_version_id=empty.id)

    def test_activity_without_usage_fails_loud(self):
        # BUD-06: demand on an activity that consumes no resource would drop cost on the floor
        usage = [u for u in self._usage() if u["activity_id"] != self.ship.id]
        with self.assertRaises(UserError):
            self._abb_run(usage=usage)

    def test_resource_without_driver_or_element_fails_loud(self):
        # BUD-06: no unit -> no rate; no element -> no budget line
        self.truck.with_user(self.manager_a).driver_id = False
        with self.assertRaises(UserError):
            self._abb_run()
        self.truck.with_user(self.manager_a).write({"driver_id": self.shipments.id, "cost_element_ids": [(5, 0, 0)]})
        with self.assertRaises(UserError):
            self._abb_run()
        self.truck.with_user(self.manager_a).budget_element_id = self.trk
        run = self._abb_run()
        self.assertEqual(self.env["cmc.budget.line"].search([("abb_resource_id", "=", self.truck.id)]).element_id, self.trk)
        self.assertEqual(run.state, "done")

    def test_computed_run_locks_inputs(self):
        # AD-001: bill, usage and versions are inputs of the certified budget
        run = self._abb_run()
        with self.assertRaises(UserError):
            run.bill_line_ids[0].qty_per_unit = 9
        with self.assertRaises(UserError):
            self.env_a["cmc.abb.usage.line"].create({"run_id": run.id, "activity_id": self.setup.id,
                                                     "resource_id": self.energy.id, "usage_per_unit": 1})
        with self.assertRaises(UserError):
            run.calibration_tolerance = 50

    def test_results_written_by_compute_only(self):
        # AD-001: a result typed by hand would be certified next to the engine's rows
        run = self._abb_run()
        with self.assertRaises(UserError):
            self.env_a["cmc.abb.resource.result"].create({"run_id": run.id, "resource_id": self.tech.id, "budgeted_cost": 1})
        with self.assertRaises(UserError):
            run.activity_result_ids[0].rate = 1

    def test_reset_removes_results_lines_and_rates(self):
        # AD-113: reset deletes everything the run produced, recompute rebuilds the same figures
        run = self._abb_run()
        self.assertTrue(self._target_rates())
        run.action_reset_to_draft()
        self.assertFalse(run.activity_result_ids or run.resource_result_ids or run.object_result_ids)
        self.assertFalse(self.env["cmc.budget.line"].search([("abb_run_id", "=", run.id)]))
        self.assertFalse(self._target_rates())
        self.assertFalse(run.total_budgeted_cost)
        run.action_compute()
        self.assertAlmostEqual(run.total_budgeted_cost, 178800.0, places=2)
        self.assertEqual(len(self.bud27.budget_line_ids), 48)

    def test_source_and_calibration_locked_while_computed(self):
        # AD-002: the forecast and the calibration a computed budget reads cannot be reopened under it
        self._abb_run()
        with self.assertRaises(UserError):
            self.fc27.with_user(self.manager_a).action_reset_to_draft()
        with self.assertRaises(UserError):
            self.calib.action_reset_to_draft()

    def test_approved_target_locks_the_run(self):
        # AD-104: once the budget is approved its ABB lines cannot vanish with a reset
        run = self._abb_run()
        self.bud27.with_user(self.manager_a).action_approve()
        with self.assertRaises(UserError):
            run.action_reset_to_draft()
        self.assertEqual(len(self.env["cmc.budget.line"].search([("abb_run_id", "=", run.id)])), 48)
