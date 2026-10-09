"""WP-15 / AD-114 - planning a baseline and computing a status report do not grow with the WBS.

CONTRACT (part)
- ``action_approve`` of a baseline (plan: rate lookup, phasing, baseline rows) and ``cmc.evm.run`` Compute read
  and write set-based: the query count is the same for a WBS of 4 tasks as for 40 tasks (x 3 months each),
  small constant tolerance. A per-task /
  per-line search, rate lookup or create = N+1 = red.
Volume fixture: n tasks on project P, each estimated 10/10/10 h on center Z (rate 50, rate card Jan..Mar) over
Jan..Mar -> cost 500 per task, PV 166.67/166.67/166.66 in some order; progress 50 % -> EV 250 per task.
"""

from datetime import date

from odoo.tests.common import tagged

from .evm_common import FEB, JAN, MAR, MAR_END, EvmCase

TOLERANCE = 4


@tagged("post_install", "-at_install")
class TestEvmPerformance(EvmCase):
    def _volume_baseline(self, n):
        tasks = self.su["project.task"].create([{"name": "WP15 vol %s-%s" % (n, i), "project_id": self.prj.id} for i in range(n)])
        estimates = [
            {"name": task.name, "task_id": task.id, "center_id": self.z.id, "hours_optimistic": 10, "hours_most_likely": 10,
             "hours_pessimistic": 10, "material_cost": 0.0, "date_start": JAN, "date_end": date(2026, 3, 31)}
            for task in tasks
        ]
        version = self._baseline(approve=False, risks=False, estimates=estimates,
                                 rate_card=((self.z, JAN, 50.0), (self.z, FEB, 50.0), (self.z, MAR, 50.0)))
        return version, tasks

    def _measure(self, func):
        self.env.flush_all()
        self.env.invalidate_all()
        start = self.cr.sql_log_count
        func()
        self.env.flush_all()
        return self.cr.sql_log_count - start

    def test_baseline_approve_query_count_independent_of_wbs(self):
        # AD-114: approving a 40-task baseline costs the same queries as a 4-task one
        small, _tasks = self._volume_baseline(4)
        big, _big_tasks = self._volume_baseline(40)
        budget = self._measure(small.action_approve) + TOLERANCE
        self.env.invalidate_all()
        with self.assertQueryCount(budget):
            big.action_approve()
        self.assertEqual(len(big.baseline_line_ids), 120)
        self.assertAlmostEqual(big.bac, 40 * 500.0, places=2)

    def test_status_report_query_count_independent_of_wbs(self):
        # AD-114: a status report on 40 tasks costs the same queries as on 4
        small, small_tasks = self._volume_baseline(4)
        big, big_tasks = self._volume_baseline(40)
        small.action_approve()
        big.action_approve()
        self._progress(dict.fromkeys(small_tasks | big_tasks, 50.0))
        small_run = self._evm(small, self.job_mar, MAR_END, compute=False)
        big_run = self._evm(big, self.job_mar, MAR_END, compute=False)
        budget = self._measure(small_run.action_compute) + TOLERANCE
        self.env.invalidate_all()
        with self.assertQueryCount(budget):
            big_run.action_compute()
        self.assertEqual(len(big_run.task_line_ids), 40)
        self.assertRecordValues(big_run, [{"bac": 20000.0, "pv": 20000.0, "ev": 10000.0, "ac": 17000.0}])
