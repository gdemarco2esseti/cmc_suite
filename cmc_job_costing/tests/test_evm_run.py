"""WP-15 / PRJ-04 + PRJ-03 - EVM status run: EV from task progress, AC from the job cost sheet, PV from the
baseline; indices and EAC variants; S-curve and planned vs actual per month with control limits.

CONTRACT: see ``evm_common`` (``cmc.evm.run``, ``cmc.evm.task.line``, ``cmc.evm.curve``).
"""

from odoo import Command
from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import tagged

from .evm_common import APR, FEB, FEB_END, JAN, JUN, MAR, MAR_END, MAY, EvmCase


@tagged("post_install", "-at_install")
class TestEvmRun(EvmCase):
    def test_prj04_indices_at_status_date(self):
        # PRJ-04 (PCM Ch10): BAC 32200, PV 20650, EV 16800, AC 17000 at March 31
        _jan, _feb, mar = self._history(self._baseline())
        self.assertEqual(mar.state, "done")
        self.assertRecordValues(mar, [{
            "bac": 32200.0, "pv": 20650.0, "ev": 16800.0, "ac": 17000.0, "cv": -200.0, "sv": -3850.0,
            "eac": 32583.33, "etc": 15583.33, "vac": -383.33, "eac_atypical": 32400.0, "eac_composite": 36154.51,
        }])
        self.assertAlmostEqual(mar.cpi, 16800 / 17000, places=3)
        self.assertAlmostEqual(mar.spi, 16800 / 20650, places=3)
        self.assertAlmostEqual(mar.tcpi, 15400 / 15200, places=3)

    def test_prj04_indices_of_an_earlier_status(self):
        # PRJ-04: the February report on its own figures (AC = job-to-date of the February job run)
        baseline = self._baseline()
        feb = self._evm(baseline, self.job_feb, FEB_END, {self.t1: 100.0, self.t2: 20.0, self.t3: 0.0})
        self.assertRecordValues(feb, [{
            "pv": 12950.0, "ev": 9870.0, "ac": 11000.0, "cv": -1130.0, "sv": -3080.0,
            "eac": 35886.52, "eac_atypical": 33330.0, "eac_composite": 43652.53,
        }])
        self.assertAlmostEqual(feb.tcpi, 22330 / 21200, places=3)

    def test_prj04_task_lines_ev_from_progress(self):
        # PRJ-04: EV = task BAC x % complete, PV of the task to the status date
        _jan, _feb, mar = self._history(self._baseline())
        self.assertRecordValues(mar.task_line_ids.sorted(lambda line: line.task_id.id), [
            {"task_id": self.t1.id, "bac": 5250.0, "percent_complete": 100.0, "pv": 5250.0, "ev": 5250.0, "sv": 0.0},
            {"task_id": self.t2.id, "bac": 23100.0, "percent_complete": 50.0, "pv": 15400.0, "ev": 11550.0, "sv": -3850.0},
            {"task_id": self.t3.id, "bac": 3850.0, "percent_complete": 0.0, "pv": 0.0, "ev": 0.0, "sv": 0.0},
        ])

    def test_prj04_progress_is_snapshotted(self):
        # PRJ-04: a computed status report keeps the progress it measured
        _jan, _feb, mar = self._history(self._baseline())
        self._progress({self.t2: 90.0})
        self.assertAlmostEqual(mar.ev, 16800.0, places=2)
        self.assertEqual(mar.task_line_ids.filtered(lambda line: line.task_id == self.t2).percent_complete, 50.0)

    def test_prj04_s_curve_cumulative_pv_ev_ac(self):
        # PRJ-04: one row per baseline month; EV/AC of past months from the earlier status reports
        _jan, _feb, mar = self._history(self._baseline())
        self.assertRecordValues(mar.curve_ids.sorted("date"), [
            {"date": JAN, "pv_cumulative": 2625.0, "ev_cumulative": 2625.0, "ac_cumulative": 3000.0, "is_forecast": False},
            {"date": FEB, "pv_cumulative": 12950.0, "ev_cumulative": 9870.0, "ac_cumulative": 11000.0, "is_forecast": False},
            {"date": MAR, "pv_cumulative": 20650.0, "ev_cumulative": 16800.0, "ac_cumulative": 17000.0, "is_forecast": False},
            {"date": APR, "pv_cumulative": 30275.0, "ev_cumulative": 0.0, "ac_cumulative": 0.0, "is_forecast": True},
            {"date": MAY, "pv_cumulative": 32200.0, "ev_cumulative": 0.0, "ac_cumulative": 0.0, "is_forecast": True},
            {"date": JUN, "pv_cumulative": 32200.0, "ev_cumulative": 0.0, "ac_cumulative": 0.0, "is_forecast": True},
        ])

    def test_prj03_planned_vs_actual_per_month_and_control_limits(self):
        # PRJ-03: monthly PV / EV / AC and cost variance; |cv| above the 800 limit is out of control
        _jan, _feb, mar = self._history(self._baseline())
        self.assertRecordValues(mar.curve_ids.sorted("date")[:4], [
            {"date": JAN, "pv": 2625.0, "ev": 2625.0, "ac": 3000.0, "cv": -375.0, "out_of_control": False},
            {"date": FEB, "pv": 10325.0, "ev": 7245.0, "ac": 8000.0, "cv": -755.0, "out_of_control": False},
            {"date": MAR, "pv": 7700.0, "ev": 6930.0, "ac": 6000.0, "cv": 930.0, "out_of_control": True},
            {"date": APR, "pv": 9625.0, "ev": 0.0, "ac": 0.0, "cv": 0.0, "out_of_control": False},
        ])
        self.assertRecordValues(mar, [{"out_of_control_count": 1, "rule_of_seven": False}])

    def test_prj03_no_control_limit_flags_nothing(self):
        # PRJ-03: limit 0 = no control chart band
        baseline = self._baseline()
        mar = self._evm(baseline, self.job_mar, MAR_END, {self.t1: 100.0, self.t2: 50.0, self.t3: 0.0}, control_limit=0.0)
        self.assertFalse(any(mar.curve_ids.mapped("out_of_control")))
        self.assertEqual(mar.out_of_control_count, 0)

    def test_prj04_first_report_without_history(self):
        # PRJ-04: without earlier reports the past months carry 0 and the status month holds this run
        mar = self._evm(self._baseline(), self.job_mar, MAR_END, {self.t1: 100.0, self.t2: 50.0, self.t3: 0.0})
        self.assertRecordValues(mar.curve_ids.sorted("date")[:3], [
            {"date": JAN, "ev_cumulative": 0.0, "ac_cumulative": 0.0},
            {"date": FEB, "ev_cumulative": 0.0, "ac_cumulative": 0.0},
            {"date": MAR, "ev_cumulative": 16800.0, "ac_cumulative": 17000.0},
        ])

    def test_prj04_reports_of_another_baseline_are_ignored(self):
        # PRJ-04: the S-curve history belongs to the baseline measured, a re-baseline starts its own
        old = self._baseline()
        self._evm(old, self.job_feb, FEB_END, {self.t1: 100.0, self.t2: 20.0, self.t3: 0.0})
        new = self._baseline()
        mar = self._evm(new, self.job_mar, MAR_END, {self.t1: 100.0, self.t2: 50.0})
        feb_row = mar.curve_ids.filtered(lambda r: r.date == FEB)
        self.assertRecordValues(feb_row, [{"ev_cumulative": 0.0, "ac_cumulative": 0.0}])

    def test_prj04_draft_baseline_refused(self):
        # PRJ-04: EVM measures against an approved reference only
        run = self._evm(self._baseline(approve=False), self.job_mar, MAR_END, {self.t1: 100.0}, compute=False)
        with self.assertRaises(UserError):
            run.action_compute()
        self.assertEqual(run.state, "draft")

    def test_prj04_job_run_must_match_the_status_date(self):
        # PRJ-04: AC is the job-to-date cost AT the status date
        run = self._evm(self._baseline(), self.job_feb, MAR_END, {self.t1: 100.0}, compute=False)
        with self.assertRaises(UserError):
            run.action_compute()

    def test_prj04_job_run_must_be_computed_and_hold_the_project(self):
        # PRJ-04: no computed sheet of the project = no actual cost
        baseline = self._baseline()
        draft_job = self.job_mar.copy({"name": "WP15 draft job"})
        run = self._evm(baseline, draft_job, MAR_END, {self.t1: 100.0}, compute=False)
        with self.assertRaises(UserError):
            run.action_compute()
        other_job = self._run(project_ids=[Command.set(self.j1.ids)])  # March job run of another job only
        run = self._evm(baseline, other_job, MAR_END, compute=False)
        with self.assertRaises(UserError):
            run.action_compute()

    def test_prj04_baseline_of_another_project_refused(self):
        # PRJ-04: the baseline measured is the plan of the run's project
        baseline = self._baseline()
        with self.assertRaises(ValidationError):
            self._evm(baseline, self.job_mar, MAR_END, compute=False, project_id=self.prj_q.id)

    def test_prj04_baseline_type_required(self):
        # PRJ-04: a budget version is not a project baseline
        budget = self.env["cmc.plan.version"].create(
            {"name": "WP15 budget", "company_id": self.company_a.id, "date_from": JAN, "date_to": JUN}
        )
        with self.assertRaises(ValidationError):
            self._evm(budget, self.job_mar, MAR_END, compute=False)

    def test_prj04_progress_between_zero_and_hundred(self):
        # PRJ-04: % complete is a percent
        for value in (-1.0, 101.0):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                self.t1.sudo().cmc_progress = value

    def test_prj04_sources_locked_while_report_computed(self):
        # AD-002: neither the job run (AC) nor the baseline (PV/BAC) can be reopened under a computed report
        baseline = self._baseline()
        run = self._evm(baseline, self.job_mar, MAR_END, {self.t1: 100.0, self.t2: 50.0, self.t3: 0.0})
        with self.assertRaises(UserError):
            self.job_mar.action_reset_to_draft()
        with self.assertRaises(UserError):
            baseline.action_reset_to_draft()
        run.action_reset_to_draft()
        self.assertFalse(run.task_line_ids)
        self.assertFalse(run.curve_ids)
        self.assertEqual(run.ev, 0.0)
        self.job_mar.action_reset_to_draft()
        self.assertEqual(self.job_mar.state, "draft")

    def test_prj04_inputs_locked_when_computed(self):
        # AD-001: a computed status report keeps its inputs
        run = self._evm(self._baseline(), self.job_mar, MAR_END, {self.t1: 100.0, self.t2: 50.0, self.t3: 0.0})
        with self.assertRaises(UserError):
            run.control_limit = 100.0
