"""WP-05 / FC-06 - the job cost sheet (see ``common`` CONTRACT for fields and fixture).

CONTRACT (part)
- job cost = direct costs (stamped lines, timesheets excluded) + hours x resource rate + applied overhead
  (predetermined rate x base: hours or prime cost); job revenue from the GL by project analytic (weighted by
  the distribution %); margin = revenue - job cost.
- Labour rate (table): employee-category row of the center/month first, else the center-level row; missing
  rate -> UserError (no fallback to the employee cost); two matching category rows -> UserError.
- Scope: ``project_ids`` empty = projects with activity in the period; explicit list = only those.
Numbers (manual 15/h on hours): J1 hours 35, direct 1000 + 200, labour 1650, overhead 525, cost 3375,
revenue 4900, margin 1525 (31.12%); J2 hours 8, direct 400, labour 320, overhead 120, cost 840, revenue 2100.
"""

from datetime import date

from odoo import Command
from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import tagged

from .common import D_FROM, JobCase


@tagged("post_install", "-at_install")
class TestJobSheet(JobCase):
    def test_sheet_golden_path_j1(self):
        # FC-06: job = DM + DL h x rate + overhead (base x predetermined rate); revenue and margin
        run = self._run()
        self.assertEqual(run.state, "done")
        self.assertRecordValues(self._sheet(run, self.j1), [{
            "cost_object_id": self.obj_j1.id,
            "hours": 35.0,
            "direct_material_cost": 1000.0,
            "other_direct_cost": 200.0,
            "direct_cost": 1200.0,
            "labour_cost": 1650.0,
            "prime_cost": 2850.0,
            "overhead_base_qty": 35.0,
            "overhead_applied": 525.0,
            "job_cost": 3375.0,
            "revenue": 4900.0,
            "margin": 1525.0,
            "job_status": "wip",
        }])
        self.assertAlmostEqual(self._sheet(run, self.j1).margin_percent, 31.12, places=2)

    def test_sheet_j2(self):
        run = self._run()
        self.assertRecordValues(self._sheet(run, self.j2), [{
            "hours": 8.0, "direct_cost": 400.0, "labour_cost": 320.0, "overhead_applied": 120.0,
            "job_cost": 840.0, "revenue": 2100.0, "margin": 1260.0, "margin_percent": 60.0,
        }])

    def test_timesheet_cost_not_counted_twice(self):
        # FC-06: the timesheet amount (employee cost 30/25 per hour) is replaced by hours x resource rate
        sheet = self._sheet(self._run(), self.j1)
        self.assertEqual(sheet.direct_cost, 1200.0, "the 925 of timesheet cost must not be a direct cost")
        self.assertEqual(sheet.labour_cost, 1650.0, "valued at the resource rate, not at 30/25 per hour")

    def test_labour_rows_by_center_category_month(self):
        # CEN-04: the rate actually applied is on the sheet (audit trail of the hour value)
        sheet = self._sheet(self._run(), self.j1)
        rows = sorted(
            (r.center_id.name, r.category_id.name or "", r.date, r.hours, r.rate, r.amount, r.rate_origin)
            for r in sheet.labour_ids
        )
        self.assertEqual(rows, [
            ("WP05 Y", "", D_FROM, 5.0, 60.0, 300.0, "table"),
            ("WP05 Z", "", D_FROM, 20.0, 40.0, 800.0, "table"),
            ("WP05 Z", "WP05 Senior", D_FROM, 10.0, 55.0, 550.0, "table"),
        ])

    def test_overhead_on_prime_cost(self):
        # FC-06 / Cinquini IV.2: the base of the predetermined rate can be a cost base (prime cost)
        run = self._run(overhead_base="direct_cost", overhead_rate=0.25)
        self.assertRecordValues(self._sheet(run, self.j1), [
            {"overhead_base_qty": 2850.0, "overhead_applied": 712.5, "job_cost": 3562.5}
        ])
        self.assertAlmostEqual(self._sheet(run, self.j2).overhead_applied, 180.0, places=2)

    def test_scope_activity_of_the_period(self):
        # FC-06: an empty selection = the jobs that moved in the period (J3 did not; company B never)
        run = self._run()
        self.assertEqual(run.sheet_ids.project_id, self.j1 | self.j2)

    def test_scope_explicit_projects(self):
        run = self._run(project_ids=[Command.set(self.j2.ids)])
        self.assertEqual(run.sheet_ids.project_id, self.j2)

    def test_project_without_activity_gives_zero_sheet(self):
        # FC-06: an explicitly selected idle job has a sheet with zeroes, not an error
        run = self._run(project_ids=[Command.set(self.j3.ids)])
        self.assertRecordValues(run.sheet_ids, [{"project_id": self.j3.id, "job_cost": 0.0, "revenue": 0.0,
                                                 "margin_percent": 0.0}])

    def test_period_spans_two_months_with_monthly_rates(self):
        # AD-105 / CEN-04: April hours at the April rate (42), April costs and revenue in
        run = self._run(date_to=date(2026, 4, 30))
        self.assertRecordValues(self._sheet(run, self.j1), [{
            "hours": 39.0, "labour_cost": 1818.0, "direct_cost": 2199.0, "revenue": 7900.0,
        }])

    def test_missing_rate_fails_loud(self):
        # CEN-04: an hour without rate is an error, never valued at the employee cost
        self.rate_y.unlink()
        with self.assertRaisesRegex(UserError, "WP05 Y"):
            self._run()

    def test_category_rate_wins_over_center_rate(self):
        # CEN-04: Anna is Senior on Z (55), Bruno has no category (40)
        self.rate_z_senior.hourly_rate = 65.0
        self.assertEqual(self._sheet(self._run(), self.j1).labour_cost, 10 * 65 + 20 * 40 + 5 * 60)

    def test_employee_category_without_row_uses_center_rate(self):
        # CEN-04: a category with no row on the center falls back to the center-level rate
        self.rate_z_senior.unlink()
        self.assertEqual(self._sheet(self._run(), self.j1).labour_cost, 10 * 40 + 20 * 40 + 5 * 60)

    def test_two_matching_categories_fail_loud(self):
        # CEN-04: two category rates for the same hour = ambiguous value
        lead = self.su["hr.employee.category"].create({"name": "WP05 Lead"})
        self.anna.category_ids = [Command.link(lead.id)]
        self._rate(self.su, self.z, 70.0, category=lead)
        with self.assertRaisesRegex(UserError, "WP05 Anna"):
            self._run()

    def test_dates_ordered(self):
        with self.assertRaises(ValidationError):
            self._run(compute=False, date_from=date(2026, 3, 31), date_to=date(2026, 3, 1))

    def test_negative_overhead_rate_refused(self):
        with self.assertRaises(ValidationError):
            self._run(compute=False, overhead_rate=-1.0)

    def test_project_of_other_company_refused(self):
        # AD-115: a run of company A cannot cost a job of company B
        with self.assertRaises(UserError):
            self._run(compute=False, project_ids=[Command.set(self.jb.ids)])

    def test_company_b_run(self):
        # multi-company: own employees, rates and costs
        env_b = self.env(context=dict(self.env.context, allowed_company_ids=self.company_b.ids))
        run = env_b["cmc.job.cost.run"].create({
            "name": "WP05 B", "company_id": self.company_b.id, "date_from": D_FROM, "date_to": date(2026, 3, 31),
            "overhead_rate": 0.0,
        })
        run.action_compute()
        self.assertRecordValues(run.sheet_ids, [{"project_id": self.jb.id, "hours": 3.0, "labour_cost": 210.0,
                                                 "direct_cost": 90.0, "job_cost": 300.0}])
