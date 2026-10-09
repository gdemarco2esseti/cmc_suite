"""WP-05 / AD-114 - Compute cost does not grow with jobs, timesheets, cost lines or journal items.

CONTRACT (part)
- Compute reads analytic lines, timesheets and revenue items (unbounded) with grouped reads at the job x
  center x employee/category x month grain, resolves cost objects and rates in batches and creates sheets and
  labour rows in batches: the query count is the same for 3 jobs x 2 hours lines as for 30 jobs x 6 hours
  lines + 30 revenue lines (small constant tolerance). A per-job / per-line search or browse = N+1 = red.
Volume fixture: each job has 1 direct MP line of 10, ``k`` timesheets of 1 h (Bruno, center Z, 40/h) and one
revenue line of 100 on its analytic -> per job: hours k, labour 40k, overhead 15k, revenue 100.
"""

from odoo import Command
from odoo.tests.common import tagged

from .common import D_FROM, D_MID, D_TO, JobCase

TOLERANCE = 4


@tagged("post_install", "-at_install")
class TestJobPerformance(JobCase):
    def _volume_run(self, n, k):
        su = self.su
        analytics = su["account.analytic.account"].create(
            [{"name": "WP05 vol %s-%s" % (n, i), "plan_id": self.project_plan.id, "company_id": self.company_a.id}
             for i in range(n)]
        )
        projects = su["project.project"].create(
            [{"name": "WP05 vol job %s-%s" % (n, i), "account_id": aa.id, self.ccol: self.z.id,
              "company_id": self.company_a.id, "allow_timesheets": True} for i, aa in enumerate(analytics)]
        )
        objects = su["cmc.cost.object"]._cmc_ensure_objects(projects, company=self.company_a, object_type="project")
        AL = su["account.analytic.line"]
        AL.create([
            {"name": "direct", "account_id": aa.id, "amount": -10.0, "company_id": self.company_a.id,
             "cmc_cost_element_id": self.mp.id, "cmc_cost_object_id": obj.id, "cmc_directness": "direct",
             "cmc_behavior": "variable", "date": D_MID}
            for aa, obj in zip(analytics, objects, strict=True)
        ])
        AL.create([
            {"name": "work", "project_id": project.id, "employee_id": self.bruno.id, "unit_amount": 1.0, "date": D_MID}
            for project in projects for _i in range(k)
        ])
        self._invoice(self.company_data, [(100.0, {aa: 100}) for aa in analytics], D_MID)
        return self.env["cmc.job.cost.run"].create({
            "name": "WP05 volume %s" % n, "company_id": self.company_a.id, "date_from": D_FROM, "date_to": D_TO,
            "project_ids": [Command.set(projects.ids)], "overhead_rate": 15.0, "overhead_base": "hours",
            "overhead_center_ids": [Command.set(self.oh.ids)],
        }), projects

    def test_compute_query_count_independent_of_volume(self):
        # AD-114: 30 jobs with 6 hour lines each cost the same queries as 3 jobs with 2
        small, _projects = self._volume_run(3, 2)
        big, big_projects = self._volume_run(30, 6)
        self.env.flush_all()
        self.env.invalidate_all()
        start = self.cr.sql_log_count
        small.action_compute()
        self.env.flush_all()
        budget = self.cr.sql_log_count - start + TOLERANCE
        self.env.invalidate_all()
        with self.assertQueryCount(budget):
            big.action_compute()
        # the volume fixture is also correct, not only fast
        self.assertEqual(len(big.sheet_ids), 30)
        sheet = big.sheet_ids.filtered(lambda s: s.project_id == big_projects[0])
        self.assertRecordValues(sheet, [{"hours": 6.0, "labour_cost": 240.0, "direct_cost": 10.0,
                                         "overhead_applied": 90.0, "job_cost": 340.0, "revenue": 100.0}])
