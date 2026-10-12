from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase, tagged
from odoo.tools import mute_logger


@tagged("post_install", "-at_install")
class TestJointAccess(TransactionCase):
    """ACL and record-rule coverage: the rest of the suite runs as superuser,
    which cannot see an access-rights defect."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Run = cls.env["cmc.joint.run"]
        cls.company_a = cls.env.company
        cls.company_b = cls.env["res.company"].create({"name": "CMC Company B"})
        cls.pA = cls.env["product.product"].create({"name": "A"})
        cls.pB = cls.env["product.product"].create({"name": "B"})
        cls.user_a = cls._new_cmc_user("cmc_user_a", cls.company_a)
        cls.user_b = cls._new_cmc_user("cmc_user_b", cls.company_b)

    @classmethod
    def _new_cmc_user(cls, login, company):
        return cls.env["res.users"].create({
            "name": login,
            "login": login,
            "company_id": company.id,
            "company_ids": [(6, 0, company.ids)],
            "group_ids": [(6, 0, [
                cls.env.ref("base.group_user").id,
                cls.env.ref("cmc_base.group_cmc_user").id,
            ])],
        })

    def _make_run(self, company):
        return self.Run.create({
            "name": "JC", "method": "physical", "joint_cost": 1000,
            "company_id": company.id,
            "line_ids": [
                (0, 0, {"product_id": self.pA.id, "quantity": 60}),
                (0, 0, {"product_id": self.pB.id, "quantity": 40}),
            ],
        })

    # a plain controlling user must be able to run the allocation end to end:
    # it creates and deletes cmc.joint.result rows and writes the run state
    def test_cmc_user_can_compute(self):
        run = self._make_run(self.company_a).with_user(self.user_a)
        run.action_compute()
        self.assertEqual(run.state, "done")
        self.assertEqual(len(run.result_ids), 2)
        self.assertEqual(
            run.result_ids.filtered(
                lambda r: r.product_id == self.pA
            ).allocated_joint_cost,
            600.0,
        )
        # reset + recompute exercise the result unlink path with the same user
        run.action_reset_to_draft()
        self.assertFalse(run.result_ids)
        run.action_compute()
        self.assertEqual(len(run.result_ids), 2)

    # ... but deleting a run is a manager privilege (perm_unlink=0)
    @mute_logger("odoo.addons.base.models.ir_model")
    def test_cmc_user_cannot_unlink_run(self):
        run = self._make_run(self.company_a)
        with self.assertRaises(AccessError):
            run.with_user(self.user_a).unlink()

    # a run of company A is out of reach for a user of company B
    def test_multi_company_isolation(self):
        run = self._make_run(self.company_a)
        visible = self.Run.with_user(self.user_b).search([])
        self.assertNotIn(run, visible)
        self.assertIn(run, self.Run.with_user(self.user_a).search([]))

    def test_multi_company_isolation_lines_and_results(self):
        run = self._make_run(self.company_a)
        run.action_compute()
        self.assertFalse(
            self.env["cmc.joint.line"].with_user(self.user_b).search(
                [("run_id", "=", run.id)]
            )
        )
        self.assertFalse(
            self.env["cmc.joint.result"].with_user(self.user_b).search(
                [("run_id", "=", run.id)]
            )
        )
