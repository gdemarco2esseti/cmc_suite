"""AD-106 - cmc_hr_timesheet stamps the project as cost object (wave A0).

CONTRACT
- Every timesheet line (``project_id`` set) carries ``account.analytic.line.cmc_cost_object_id`` =
  ``cmc.cost.object`` of type ``project`` for ``project_id``, in the line's company; re-stamped when the
  project changes; a line without project is not stamped by this bridge.
- RN-002: the project of a timesheet line also changes INDIRECTLY (core recomputes ``project_id`` from
  ``task_id``): moving the task to another project, or writing only ``task_id``, must re-stamp too.
- A timesheet user without any CMC group gets the line stamped (object created by the system).
"""

from odoo.tests.common import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestTimesheetCostObject(TransactionCase):
    """AD-106: hours on a project are cost of that project."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.AL = cls.env["account.analytic.line"]
        cls.plan = cls.env["account.analytic.plan"].search([("parent_id", "=", False)]).filtered(
            lambda p: p._column_name() == "account_id"
        )[:1]
        cls.center = cls.env["account.analytic.account"].create(
            {"name": "Reparto TS CO", "plan_id": cls.plan.id, "cmc_center_type": "final"}
        )
        cls.project = cls.env["project.project"].create({"name": "Commessa CO", "account_id": cls.center.id})
        cls.project_2 = cls.env["project.project"].create({"name": "Commessa CO 2", "account_id": cls.center.id})
        cls.emp = cls.env["hr.employee"].create({"name": "Luigi", "hourly_cost": 30.0})

    def _ts(self, env=None, **kw):
        vals = {"name": "work", "project_id": self.project.id, "employee_id": self.emp.id, "unit_amount": 2.0}
        vals.update(kw)
        return (env or self.env)["account.analytic.line"].create(vals)

    def test_timesheet_stamped_with_project(self):
        # AD-106: cmc_hr_timesheet stamps the project
        line = self._ts()
        self.assertRecordValues(
            line.cmc_cost_object_id,
            [{"object_type": "project", "res_model": "project.project", "res_id": self.project.id,
              "company_id": line.company_id.id}],
        )

    def test_project_change_restamps(self):
        # AD-106: the object follows the project of the line
        line = self._ts()
        line.write({"project_id": self.project_2.id})
        self.assertEqual(line.cmc_cost_object_id.res_id, self.project_2.id)

    def _object_of(self, project, line):
        return self.env["cmc.cost.object"].search(
            [("res_model", "=", "project.project"), ("res_id", "=", project.id), ("company_id", "=", line.company_id.id)]
        )

    def test_task_moved_to_other_project_restamps(self):
        # RN-002: moving the task moves its hours, and their cost object follows
        task = self.env["project.task"].create({"name": "Task CO", "project_id": self.project.id})
        line = self._ts(task_id=task.id)
        self.assertEqual(line.cmc_cost_object_id.res_id, self.project.id, "precondition: stamped on P1")
        task.project_id = self.project_2
        line.invalidate_recordset()
        self.assertEqual(line.project_id, self.project_2, "precondition: core moves the line with its task")
        self.assertEqual(line.cmc_cost_object_id.res_id, self.project_2.id)
        self.assertEqual(line.cmc_cost_object_id, self._object_of(self.project_2, line))

    def test_write_task_only_restamps(self):
        # RN-002: writing only the task changes the project of the line, the stamp follows
        task = self.env["project.task"].create({"name": "Task CO P1", "project_id": self.project.id})
        line = self._ts(project_id=self.project_2.id)
        self.assertEqual(line.cmc_cost_object_id.res_id, self.project_2.id, "precondition: stamped on P2")
        line.write({"task_id": task.id})
        line.invalidate_recordset()
        self.assertEqual(line.project_id, self.project, "precondition: core derives the project from the task")
        self.assertEqual(line.cmc_cost_object_id.res_id, self.project.id)
        self.assertEqual(line.cmc_cost_object_id, self._object_of(self.project, line))

    def test_same_project_same_object(self):
        # AD-106 inv.(1): one object per project and company
        self.assertEqual(self._ts().cmc_cost_object_id, self._ts(unit_amount=1.0).cmc_cost_object_id)

    def test_plain_analytic_line_not_stamped(self):
        # AD-106: no project, nothing to stamp
        line = self.AL.create({"name": "x", "account_id": self.center.id, "amount": -50.0})
        self.assertFalse(line.cmc_cost_object_id)

    def test_timesheet_user_without_cmc_rights(self):
        # AD-106: an employee logging hours has no CMC group, the stamp happens anyway
        user = new_test_user(
            self.env, login="cmc_ts_co_user", groups="base.group_user,hr_timesheet.group_hr_timesheet_user"
        )
        self.assertFalse(user.has_group("cmc_base.group_cmc_user"))
        employee = self.env["hr.employee"].create({"name": "TS user", "user_id": user.id, "hourly_cost": 20.0})
        line = self._ts(env=self.env(user=user), employee_id=employee.id)
        self.assertEqual(line.sudo().cmc_cost_object_id.res_id, self.project.id)
