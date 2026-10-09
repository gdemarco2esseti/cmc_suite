"""WP-05 / FC-06 - WIP vs completed jobs, under/over-applied overhead, run lifecycle (AD-113).

CONTRACT (part)
- ``job_status = 'completed'`` when the project is closed with ``cmc_job_close_date <= date_to`` (snapshotted on
  ``close_date``), else ``wip``; run ``wip_total`` / ``completed_total`` = sum of ``job_cost`` by status and
  ``wip_total + completed_total`` = sum of the sheets (INT-05). CMC posts no WIP/FG entry.
- RN-002: ``job_cost`` (hence WIP / completed) is JOB-TO-DATE, from the job's first cost up to ``date_to``; the
  ``period_*`` fields of the sheet hold the period part. Empty scope also carries open jobs idle in the period;
  a job closed before ``date_from`` is out of scope.
- ``overhead_actual`` = indirect cost of ``overhead_center_ids`` in the period, timesheet lines charged to a job
  excluded (RN-003); ``overhead_variance`` = PERIOD applied (sum of ``period_overhead_applied``) - actual
  (< 0 under-applied, > 0 over-applied).
- Computed run locked (inputs), reset deletes sheets/labour rows and clears the outputs.
- ``cmc_job_closed`` without ``cmc_job_close_date`` -> ValidationError.
Numbers: wip J1 3375, completed J2 840; overhead 645 vs 750 -> -105; on prime cost 892.50 -> +142.50.
"""

from datetime import date

from odoo import Command
from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import tagged

from .common import D_MID, JobCase


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
        # INT-05: the run total (a period measure, RN-002) is the sum of the sheets' period overhead
        run = self._run()
        self.assertAlmostEqual(run.overhead_applied, sum(run.sheet_ids.mapped("period_overhead_applied")), places=2)

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


@tagged("post_install", "-at_install")
class TestJobToDate(JobCase):
    """RN-002: the WIP of an open job and the cost of a completed job are job-to-date, not the period's."""

    JAN, FEB, MAR = date(2026, 1, 15), date(2026, 2, 15), date(2026, 3, 15)

    def _new_job(self, name, closed_on=None):
        su = self.su
        analytic = self._job_analytic(su, "WP05 %s" % name, self.company_a)
        project = self._project(su, name, analytic, self.z, self.company_a)
        if closed_on:
            project.write({"cmc_job_closed": True, "cmc_job_close_date": closed_on})
        obj = su["cmc.cost.object"]._cmc_ensure_objects(project, company=self.company_a, object_type="project")
        return project, analytic, obj

    def test_wip_and_completed_are_job_to_date(self):
        # RN-002: J1 open Jan-Mar at 1000/month -> WIP 3000 (period 1000); J2 closed 25/3 after 5000 -> completed 5000
        j1, aa1, obj1 = self._new_job("Commessa Delta")
        j2, aa2, obj2 = self._new_job("Commessa Epsilon", closed_on=date(2026, 3, 25))
        for day in (self.JAN, self.FEB, self.MAR):
            self._direct(self.su, aa1, obj1, 1000, self.mp, day=day)
        for day, cost in ((self.JAN, 2000), (self.FEB, 2000), (self.MAR, 1000)):
            self._direct(self.su, aa2, obj2, cost, self.mp, day=day)
        run = self._run(project_ids=[Command.set((j1 | j2).ids)])
        self.assertRecordValues(run, [{"wip_total": 3000.0, "completed_total": 5000.0}])
        self.assertRecordValues(self._sheet(run, j1), [{
            "job_status": "wip", "direct_cost": 3000.0, "job_cost": 3000.0,
            "period_direct_cost": 1000.0, "period_job_cost": 1000.0,
        }])
        self.assertRecordValues(self._sheet(run, j2), [{
            "job_status": "completed", "direct_cost": 5000.0, "job_cost": 5000.0,
            "period_direct_cost": 1000.0, "period_job_cost": 1000.0,
        }])
        self.assertAlmostEqual(run.wip_total + run.completed_total, sum(run.sheet_ids.mapped("job_cost")), places=2)

    def test_earlier_hours_valued_at_their_month_rate(self):
        # RN-002 / CEN-04: February hours of J1 are job-to-date labour at the February rate; overhead variance stays the period's
        self._rate(self.su, self.z, 44.0, day=self.FEB)
        self._ts(self.su, self.j1, self.bruno, 10, day=self.FEB)
        self._indirect(self.su, self.oh, 333, "fixed", day=self.FEB)  # February overhead: not March's actual
        run = self._run()
        self.assertRecordValues(self._sheet(run, self.j1), [{
            "hours": 45.0, "labour_cost": 1650.0 + 440.0, "overhead_applied": 525.0 + 150.0,
            "job_cost": 3375.0 + 440.0 + 150.0,
            "period_hours": 35.0, "period_labour_cost": 1650.0, "period_overhead_applied": 525.0,
            "period_job_cost": 3375.0,
        }])
        self.assertRecordValues(run, [{"overhead_applied": 645.0, "overhead_actual": 750.0, "overhead_variance": -105.0,
                                       "wip_total": 3375.0 + 590.0}])

    def test_job_to_date_revenue_and_margin(self):
        # RN-002: the job margin compares job-to-date revenue with job-to-date cost; the period revenue is shown apart
        self._invoice(self.company_data, [(2000.0, {self.aa1: 100})], self.FEB)
        sheet = self._sheet(self._run(), self.j1)
        self.assertRecordValues(sheet, [{"revenue": 6900.0, "period_revenue": 4900.0, "margin": 6900.0 - 3375.0}])

    def test_open_job_idle_in_period_stays_in_wip(self):
        # RN-002: an open job with January cost only is still WIP at the end of March (empty scope)
        idle, aa, obj = self._new_job("Commessa Zeta")
        self._direct(self.su, aa, obj, 1000, self.mp, day=self.JAN)
        run = self._run()
        self.assertRecordValues(self._sheet(run, idle), [{
            "job_status": "wip", "job_cost": 1000.0, "period_job_cost": 0.0,
        }])
        self.assertEqual(run.wip_total, 3375.0 + 1000.0)

    def test_job_closed_before_period_out_of_scope(self):
        # RN-002: a job transferred to finished goods in February is no WIP nor completion of March
        old, aa, obj = self._new_job("Commessa Eta", closed_on=date(2026, 2, 20))
        self._direct(self.su, aa, obj, 1000, self.mp, day=self.JAN)
        run = self._run()
        self.assertNotIn(old, run.sheet_ids.project_id)
        self.assertEqual(run.completed_total, 840.0)


@tagged("post_install", "-at_install")
class TestJobOverheadNoDoubleCount(JobCase):
    """RN-003: hours charged to a job on a non-final center are job labour, not overhead actual."""

    def test_job_hours_on_auxiliary_center_not_overhead_actual(self):
        # RN-003: 10 h at AUX (employee 30/h, rate 40), AUX in the pool, rate 0 -> labour 400, actual 0, variance 0
        su = self.su
        aux = su["account.analytic.account"].create(
            {"name": "WP05 AUX", "plan_id": self.center_plan.id, "cmc_center_type": "auxiliary",
             "company_id": self.company_a.id}
        )
        self._rate(su, aux, 40.0)
        dario = self._employee(su, "WP05 Dario", 30.0, self.company_a)
        line = su["account.analytic.line"].create({
            "name": "work", "project_id": self.j3.id, "employee_id": dario.id, "unit_amount": 10.0, "date": D_MID,
            self.ccol: aux.id, "cmc_directness": "indirect", "cmc_behavior": "fixed",
        })
        self.assertRecordValues(line, [{"amount": -300.0, "cmc_directness": "indirect", "cmc_cost_object_id": self.obj_j3.id}])
        run = self._run(project_ids=[Command.set(self.j3.ids)], overhead_rate=0.0,
                        overhead_center_ids=[Command.set(aux.ids)])
        self.assertRecordValues(self._sheet(run, self.j3), [{"hours": 10.0, "labour_cost": 400.0, "overhead_applied": 0.0}])
        self.assertRecordValues(run, [{"overhead_applied": 0.0, "overhead_actual": 0.0, "overhead_variance": 0.0}])

    def test_unassigned_hours_on_overhead_center_still_actual(self):
        # RN-003 boundary: an indirect cost of the pool that is NOT job time stays overhead actual
        run = self._run()
        self.assertEqual(run.overhead_actual, 750.0)
