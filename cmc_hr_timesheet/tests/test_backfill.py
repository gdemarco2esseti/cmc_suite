"""RN-001 - backfill of the project cost object on timesheet lines booked before the live bridge (AD-106).

CONTRACT
- Migration ``cmc_hr_timesheet/migrations/19.0.1.4.0/post-migrate.py`` ``migrate(cr, version)`` (manifest bumped
  to >= 19.0.1.4.0): every EXISTING ``account.analytic.line`` with ``project_id`` set and no
  ``cmc_cost_object_id`` gets the ``cmc.cost.object`` of type ``project`` of its ``project_id`` in the LINE's
  company (same rule as the live bridge ``_cmc_stamp_project_object``), creating missing objects.
- Lines without project are untouched; an already-stamped line is never overwritten. Idempotent.
- Batched per company (one ``_cmc_ensure_objects`` and grouped writes per company): the query count does not
  grow with the number of lines.
"""

import importlib.util
import os

from odoo.modules import get_module_path
from odoo.modules.module import get_manifest
from odoo.tests.common import TransactionCase, tagged
from odoo.tools import parse_version

MIGRATION_VERSION = "19.0.1.4.0"


def _migration_path():
    return os.path.join(get_module_path("cmc_hr_timesheet"), "migrations", MIGRATION_VERSION, "post-migrate.py")


def _load_migrate():
    # why: the migrations folder is named after a version, not importable
    spec = importlib.util.spec_from_file_location("cmc_hr_timesheet_post_migrate_1_4_0", _migration_path())
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.migrate


@tagged("post_install", "-at_install")
class TestTimesheetBackfill(TransactionCase):
    """RN-001: hours booked before the bridge existed are job cost too."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.CO = cls.env["cmc.cost.object"]
        cls.plan = cls.env["account.analytic.plan"].search([("parent_id", "=", False)]).filtered(
            lambda p: p._column_name() == "account_id"
        )[:1]
        cls.company_b = cls.env["res.company"].create({"name": "CMC TS backfill B"})
        cls.env.user.company_ids = [(4, cls.company_b.id)]
        cls.analytic = cls.env["account.analytic.account"].create({"name": "Backfill AA", "plan_id": cls.plan.id})
        cls.project = cls.env["project.project"].create({"name": "Backfill job", "account_id": cls.analytic.id})
        cls.project_2 = cls.env["project.project"].create({"name": "Backfill job 2", "account_id": cls.analytic.id})
        cls.emp = cls.env["hr.employee"].create({"name": "Backfill Luigi", "hourly_cost": 30.0})
        env_b = cls.env(context=dict(cls.env.context, allowed_company_ids=cls.company_b.ids))
        cls.analytic_b = env_b["account.analytic.account"].create(
            {"name": "Backfill AA B", "plan_id": cls.plan.id, "company_id": cls.company_b.id}
        )
        cls.project_b = env_b["project.project"].create(
            {"name": "Backfill job B", "account_id": cls.analytic_b.id, "company_id": cls.company_b.id}
        )
        cls.emp_b = env_b["hr.employee"].create({"name": "Backfill Carla", "hourly_cost": 10.0, "company_id": cls.company_b.id})
        cls.env_b = env_b

    def _ts(self, project=None, env=None, **kw):
        # the real producer: hr_timesheet values the line (the live bridge stamps it, blanked below)
        vals = {"name": "old work", "project_id": (project or self.project).id, "employee_id": self.emp.id, "unit_amount": 2.0}
        vals.update(kw)
        return (env or self.env)["account.analytic.line"].create(vals)

    def _blank(self, lines):
        # why SQL: reproduce the lines booked before the bridge, which never went through its create()
        self.env.flush_all()
        self.env.cr.execute("UPDATE account_analytic_line SET cmc_cost_object_id = NULL WHERE id IN %s", (tuple(lines.ids),))
        self.env.invalidate_all()

    def _object_of(self, project, company):
        return self.CO.search([("res_model", "=", "project.project"), ("res_id", "=", project.id), ("company_id", "=", company.id)])

    def _migrate(self):
        self.assertTrue(os.path.isfile(_migration_path()), "cmc_hr_timesheet/migrations/%s/post-migrate.py is missing" % MIGRATION_VERSION)
        self.env.flush_all()
        _load_migrate()(self.env.cr, "19.0.1.3.0")
        self.env.invalidate_all()

    def test_manifest_version_bumped(self):
        # RN-001: the migration only runs if the installed version moves to 19.0.1.4.0
        self.assertGreaterEqual(parse_version(get_manifest("cmc_hr_timesheet")["version"]), parse_version(MIGRATION_VERSION))

    def test_unstamped_timesheet_gets_project_object(self):
        # RN-001: an old timesheet line is stamped with its project
        line = self._ts()
        self._blank(line)
        self.assertFalse(line.cmc_cost_object_id, "precondition: blanked")
        self._migrate()
        self.assertEqual(line.cmc_cost_object_id, self._object_of(self.project, line.company_id))
        self.assertRecordValues(line.cmc_cost_object_id, [{"object_type": "project", "res_id": self.project.id}])

    def test_missing_object_created(self):
        # RN-001: a project never costed before has no object yet: the migration creates it
        line = self._ts(project=self.project_2)
        self._blank(line)
        self.env.cr.execute("DELETE FROM cmc_cost_object WHERE res_model = 'project.project' AND res_id = %s", (self.project_2.id,))
        self.env.invalidate_all()
        self.assertFalse(self._object_of(self.project_2, line.company_id), "precondition: no object")
        self._migrate()
        self.assertEqual(line.cmc_cost_object_id, self._object_of(self.project_2, line.company_id))
        self.assertTrue(line.cmc_cost_object_id)

    def test_object_in_line_company(self):
        # RN-001 / AD-115: batched per company, the object of a company-B line lives in company B
        line_a = self._ts()
        line_b = self._ts(project=self.project_b, env=self.env_b, employee_id=self.emp_b.id)
        self._blank(line_a | line_b)
        self._migrate()
        self.assertEqual(line_b.cmc_cost_object_id.company_id, self.company_b)
        self.assertEqual(line_b.cmc_cost_object_id, self._object_of(self.project_b, self.company_b))
        self.assertEqual(line_a.cmc_cost_object_id, self._object_of(self.project, line_a.company_id))

    def test_line_without_project_untouched(self):
        # RN-001: a plain analytic cost is not job cost
        line = self.env["account.analytic.line"].create({"name": "x", "account_id": self.analytic.id, "amount": -50.0})
        self._migrate()
        self.assertFalse(line.cmc_cost_object_id)

    def test_existing_stamp_not_overwritten(self):
        # RN-001: a line already stamped (by a rule or by hand) keeps its object
        line = self._ts()
        other = self._ts(project=self.project_2).cmc_cost_object_id
        self.env.flush_all()
        self.env.cr.execute("UPDATE account_analytic_line SET cmc_cost_object_id = %s WHERE id = %s", (other.id, line.id))
        self.env.invalidate_all()
        self._migrate()
        self.assertEqual(line.cmc_cost_object_id, other)

    def test_idempotent(self):
        # RN-001: running twice changes nothing and creates no duplicate object
        line = self._ts()
        self._blank(line)
        self._migrate()
        stamped = line.cmc_cost_object_id
        self._migrate()
        self.assertEqual(line.cmc_cost_object_id, stamped)
        self.assertEqual(len(self._object_of(self.project, line.company_id)), 1)

    def test_query_count_independent_of_volume(self):
        # RN-001 / AD-114: the backfill runs on the whole timesheet history: batched, never one query per line
        self._migrate()  # clean slate: anything pre-existing in the database is stamped now

        def count(n):
            projects = self.env["project.project"].create(
                [{"name": "Backfill vol %s-%s" % (n, i), "account_id": self.analytic.id} for i in range(n)]
            )
            lines = self.env["account.analytic.line"].create([
                {"name": "old", "project_id": p.id, "employee_id": self.emp.id, "unit_amount": 1.0}
                for p in projects for _i in range(3)
            ])
            self._blank(lines)
            before = self.env.cr.sql_log_count
            self._migrate()
            spent = self.env.cr.sql_log_count - before
            self.assertEqual(len(lines.filtered("cmc_cost_object_id")), len(lines))
            return spent

        small = count(2)
        large = count(20)
        self.assertLessEqual(large, small + 4, "60 lines on 20 projects must cost the same queries as 6 lines on 2")
