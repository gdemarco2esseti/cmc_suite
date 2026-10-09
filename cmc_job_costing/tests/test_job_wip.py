"""WP-05 / FC-06 - WIP vs completed jobs, under/over-applied overhead, run lifecycle (AD-113).

CONTRACT (part)
- ``job_status = 'completed'`` when the project is closed with ``cmc_job_close_date <= date_to`` (snapshotted on
  ``close_date``), else ``wip``; run ``wip_total`` / ``completed_total`` = sum of ``job_cost`` by status and
  ``wip_total + completed_total`` = sum of the sheets (INT-05). CMC posts no WIP/FG entry.
- ``overhead_actual`` = indirect cost of ``overhead_center_ids`` in the period; ``overhead_variance`` =
  applied - actual (< 0 under-applied, > 0 over-applied).
- Computed run locked (inputs), reset deletes sheets/labour rows and clears the outputs.
- ``cmc_job_closed`` without ``cmc_job_close_date`` -> ValidationError.
Numbers: wip J1 3375, completed J2 840; overhead 645 vs 750 -> -105; on prime cost 892.50 -> +142.50.
"""

from datetime import date

from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import tagged

from .common import JobCase


@tagged("post_install", "-at_install")
class TestJobWip(JobCase):
    def test_wip_and_completed_totals(self):
        # FC-06: open jobs stay in WIP, closed jobs are transferred to finished goods
        run = self._run()
        self.assertRecordValues(self._sheet(run, self.j2), [{"job_status": "completed", "close_date": date(2026, 3, 25)}])
        self.assertRecordValues(run, [{"wip_total": 3375.0, "completed_total": 840.0}])
        self.assertAlmostEqual(run.wip_total + run.completed_total, sum(run.sheet_ids.mapped("job_cost")), places=2)

    def test_closed_after_period_end_is_wip(self):
        # FC-06: a job closed in April is still work in progress at the end of March
        self.su["project.project"].browse(self.j1.id).write(
            {"cmc_job_closed": True, "cmc_job_close_date": date(2026, 4, 10)}
        )
        run = self._run()
        self.assertEqual(self._sheet(run, self.j1).job_status, "wip")
        self.assertFalse(self._sheet(run, self.j1).close_date)

    def test_close_date_is_snapshot(self):
        # AD-113: reopening the project later does not rewrite the computed sheet
        run = self._run()
        self.su["project.project"].browse(self.j2.id).write({"cmc_job_closed": False, "cmc_job_close_date": False})
        self.assertEqual(self._sheet(run, self.j2).job_status, "completed")

    def test_closed_needs_date(self):
        with self.assertRaises(ValidationError):
            self.su["project.project"].browse(self.j1.id).write({"cmc_job_closed": True})

    def test_under_applied_overhead(self):
        # FC-06 / BB 1.6: applied (predetermined rate x actual base) vs actual overhead of the period
        run = self._run()
        self.assertRecordValues(run, [{"overhead_applied": 645.0, "overhead_actual": 750.0, "overhead_variance": -105.0}])

    def test_over_applied_overhead(self):
        run = self._run(overhead_base="direct_cost", overhead_rate=0.25)
        self.assertRecordValues(run, [{"overhead_applied": 892.5, "overhead_actual": 750.0, "overhead_variance": 142.5}])

    def test_applied_overhead_conserved(self):
        # INT-05: the run total is the sum of the sheets
        run = self._run()
        self.assertAlmostEqual(run.overhead_applied, sum(run.sheet_ids.mapped("overhead_applied")), places=2)

    def test_computed_run_locked(self):
        # AD-001: inputs of a computed run cannot change
        run = self._run()
        with self.assertRaises(UserError):
            run.overhead_rate = 20.0

    def test_reset_clears_results(self):
        # AD-001: reset deletes sheets and labour rows and clears the outputs
        run = self._run()
        run.action_reset_to_draft()
        self.assertRecordValues(run, [{"state": "draft", "overhead_applied": 0.0, "overhead_actual": 0.0,
                                       "overhead_variance": 0.0, "wip_total": 0.0, "completed_total": 0.0}])
        self.assertFalse(run.sheet_ids)
        self.assertFalse(run.labour_ids)
        run.overhead_rate = 20.0
        run.action_compute()
        self.assertEqual(self._sheet(run, self.j1).overhead_applied, 700.0)

    def test_results_written_by_compute_only(self):
        # AD-001: a sheet is a result row
        run = self._run()
        with self.assertRaises(UserError):
            self._sheet(run, self.j1).write({"job_cost": 1.0})
