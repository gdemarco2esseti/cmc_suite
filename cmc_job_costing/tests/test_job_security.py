"""WP-05 / AD-115 - roles act with their own user (with_user), two companies.

CONTRACT (part)
- A ``cmc_base.group_cmc_user`` of company A (also project user, no accounting / HR rights) creates and
  computes a run: the engine reads lines, timesheets, employees, rates and the GL in sudo inside the run
  company; figures identical to the superuser ones. The user maintains ``cmc.job.rate`` (create/write).
- The user cannot write a sheet (read-only ACL -> AccessError).
- Without a CMC group: no run, no rate table (AccessError).
- A CMC user of company B sees neither the runs nor the sheets nor the rates of company A.
- Closing a job (``cmc_job_closed`` / ``cmc_job_close_date``) = ``group_cmc_manager`` only, even for a
  project manager (AccessError); the CMC manager closes it.
"""

from datetime import date

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests.common import new_test_user, tagged

from .common import D_MID, JobCase


@tagged("post_install", "-at_install")
class TestJobSecurity(JobCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        groups_user = "base.group_user,project.group_project_user,cmc_base.group_cmc_user"
        cls.user_a = new_test_user(
            cls.env, login="wp05_cmc_a", groups=groups_user,
            company_id=cls.company_a.id, company_ids=[Command.set(cls.company_a.ids)],
        )
        cls.user_b = new_test_user(
            cls.env, login="wp05_cmc_b", groups=groups_user,
            company_id=cls.company_b.id, company_ids=[Command.set(cls.company_b.ids)],
        )
        cls.outsider = new_test_user(
            cls.env, login="wp05_outsider", groups="base.group_user,project.group_project_user",
            company_id=cls.company_a.id, company_ids=[Command.set(cls.company_a.ids)],
        )
        cls.pm_user = new_test_user(
            cls.env, login="wp05_pm_cmc_user",
            groups="base.group_user,project.group_project_manager,cmc_base.group_cmc_user",
            company_id=cls.company_a.id, company_ids=[Command.set(cls.company_a.ids)],
        )
        cls.cmc_manager = new_test_user(
            cls.env, login="wp05_cmc_manager",
            groups="base.group_user,project.group_project_manager,cmc_base.group_cmc_manager",
            company_id=cls.company_a.id, company_ids=[Command.set(cls.company_a.ids)],
        )

    def test_cmc_user_computes_run(self):
        # FC-06 as the controller who presses Compute
        run = self._run(env=self._env_a(self.user_a))
        self.assertEqual(run.state, "done")
        self.assertRecordValues(run.sheet_ids.sorted(lambda s: s.project_id.name), [
            {"project_id": self.j1.id, "labour_cost": 1650.0, "job_cost": 3375.0, "revenue": 4900.0},
            {"project_id": self.j2.id, "labour_cost": 320.0, "job_cost": 840.0, "revenue": 2100.0},
        ])
        self.assertEqual(run.overhead_variance, -105.0)

    def test_cmc_user_maintains_rate_table(self):
        # CEN-04: the controller keeps the rate card
        rate = self._rate(self._env_a(self.user_a), self.oh, 33.0)
        rate.hourly_rate = 34.0
        self.assertEqual(rate.hourly_rate, 34.0)

    def test_cmc_user_cannot_write_sheet(self):
        run = self._run(env=self._env_a(self.user_a))
        with self.assertRaises(AccessError):
            self._sheet(run, self.j1).write({"job_cost": 1.0})

    def test_outsider_cannot_create_run(self):
        with self.assertRaises(AccessError):
            self._run(compute=False, env=self._env_a(self.outsider))

    def test_outsider_cannot_read_rates(self):
        with self.assertRaises(AccessError):
            self.env["cmc.job.rate"].with_user(self.outsider).search([])

    def test_company_b_user_sees_nothing_of_a(self):
        # AD-115: record rules per company
        run = self._run()
        env_b = self.env(user=self.user_b, context=dict(self.env.context, allowed_company_ids=self.company_b.ids))
        self.assertFalse(env_b["cmc.job.cost.run"].search([("id", "=", run.id)]))
        self.assertFalse(env_b["cmc.job.sheet"].search([("run_id", "=", run.id)]))
        self.assertFalse(env_b["cmc.job.rate"].search([("id", "=", self.rate_z.id)]))

    def test_project_manager_cannot_close_job(self):
        # FC-06: the transfer to finished goods is a controlling decision
        with self.assertRaises(AccessError):
            self.j1.with_user(self.pm_user).write({"cmc_job_closed": True, "cmc_job_close_date": D_MID})

    def test_cmc_manager_closes_job(self):
        project = self.j1.with_user(self.cmc_manager)
        project.write({"cmc_job_closed": True, "cmc_job_close_date": date(2026, 3, 30)})
        self.assertTrue(project.cmc_job_closed)
        run = self._run(env=self._env_a(self.cmc_manager))
        self.assertEqual(self._sheet(run, self.j1).job_status, "completed")

    def test_project_manager_edits_other_project_fields(self):
        # the guard is on the job-closing fields only, the project stays the PM's
        self.j1.with_user(self.pm_user).write({"name": "Commessa Alfa bis"})
        self.assertEqual(self.j1.name, "Commessa Alfa bis")
