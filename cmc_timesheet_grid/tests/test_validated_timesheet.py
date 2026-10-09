from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestValidatedTimesheet(TransactionCase):
    """timesheet_grid refuses any write on a validated timesheet to a user who is
    not a timesheet approver, checking env.user even under sudo(): the CMC
    classification gate must not fail on it."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        plan = cls.env["account.analytic.plan"].search(
            [("parent_id", "=", False)]
        ).filtered(lambda p: p._column_name() == "account_id")[:1]
        center = cls.env["account.analytic.account"].create(
            {"name": "Reparto GRID", "plan_id": plan.id, "cmc_center_type": "final"}
        )
        project = cls.env["project.project"].create(
            {"name": "Commessa GRID", "account_id": center.id}
        )
        employee = cls.env["hr.employee"].create({"name": "Luca", "hourly_cost": 30.0})
        cls.line = cls.env["account.analytic.line"].create({
            "name": "work", "project_id": project.id,
            "employee_id": employee.id, "unit_amount": 2.0, "validated": True,
        })
        cls.controller = new_test_user(
            cls.env, login="cmc_grid_controller",
            groups="base.group_user,cmc_base.group_cmc_user",
        )
        cls.element = cls.env["cmc.cost.element"].create({"name": "Elemento GRID", "code": "GRID"})

    def test_controller_classifies_a_validated_timesheet(self):
        self.assertTrue(self.line.validated)
        self.assertFalse(self.controller.has_group("hr_timesheet.group_hr_timesheet_approver"))
        self.line.with_user(self.controller).write({
            "cmc_cost_element_id": self.element.id, "cmc_behavior": "fixed",
        })
        self.assertTrue(self.line.cmc_classified_manually)
        self.assertEqual(self.line.cmc_cost_element_id, self.element)

    def test_controller_cannot_touch_other_fields_of_a_validated_timesheet(self):
        with self.assertRaises(AccessError):
            self.line.with_user(self.controller).write({"unit_amount": 5.0})
