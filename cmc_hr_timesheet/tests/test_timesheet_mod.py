from odoo.tests.common import TransactionCase


class TestTimesheetMod(TransactionCase):
    """Fase 1.5b — feed MOD (direct labor) from hr_timesheet into cost lines."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.AL = cls.env["account.analytic.line"]
        cls.plan = cls.env["account.analytic.plan"].search(
            [("parent_id", "=", False)]
        ).filtered(lambda p: p._column_name() == "account_id")[:1]
        cls.center = cls.env["account.analytic.account"].create(
            {"name": "Reparto", "plan_id": cls.plan.id, "cmc_center_type": "final"}
        )
        cls.emp = cls.env["hr.employee"].create(
            {"name": "Mario", "hourly_cost": 30.0}
        )
        cls.project = cls.env["project.project"].create(
            {"name": "Commessa", "account_id": cls.center.id}
        )
        cls.mod = cls.env.ref("cmc_base.cmc_element_mod")

    def _ts(self, **kw):
        vals = {"name": "work", "project_id": self.project.id,
                "employee_id": self.emp.id, "unit_amount": 2.0}
        vals.update(kw)
        return self.AL.create(vals)

    # MOD line: element=MOD, behavior=variable, directness=direct
    def test_timesheet_mod_classified(self):
        line = self._ts()
        self.assertEqual(line.cmc_cost_element_id, self.mod)
        self.assertEqual(line.cmc_behavior, "variable")
        self.assertEqual(line.cmc_directness, "direct")

    # manual classification on a timesheet is never overwritten
    def test_manual_not_overwritten(self):
        line = self._ts(cmc_behavior="fixed")
        self.assertTrue(line.cmc_classified_manually)
        self.assertEqual(line.cmc_behavior, "fixed")

    # a non-timesheet analytic line (no project/employee) is not tagged MOD
    def test_non_timesheet_not_mod(self):
        line = self.AL.create(
            {"name": "x", "account_id": self.center.id, "amount": -50.0}
        )
        self.assertNotEqual(line.cmc_cost_element_id, self.mod)
        self.assertFalse(line.cmc_behavior)

    # no double imputation: classifying does not create extra lines
    def test_no_double_imputation(self):
        before = self.AL.search_count([("account_id", "=", self.center.id)])
        self._ts()
        after = self.AL.search_count([("account_id", "=", self.center.id)])
        self.assertEqual(after, before + 1)
