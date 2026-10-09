from datetime import date

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, new_test_user, tagged

from odoo.addons.cmc_base.models.cmc_run_mixin import CMC_RUN_STATE


@tagged("post_install", "-at_install")
class TestRunLifecycle(TransactionCase):
    """AD-001 on the closing run: a computed run is locked, the only way back
    is a reset that deletes its results."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        plan = cls.env["account.analytic.plan"].search([("parent_id", "=", False)]).filtered(
            lambda p: p._column_name() == "account_id"
        )[:1]
        cls.center = cls.env["account.analytic.account"].create(
            {"name": "Final LC", "plan_id": plan.id, "cmc_center_type": "final"}
        )
        year = date.today().year
        cls.env["account.analytic.line"].create(
            {"name": "cost", "account_id": cls.center.id, "amount": -500, "date": date(year, 6, 15)}
        )
        cls.period = {"date_from": date(year, 1, 1), "date_to": date(year, 12, 31)}
        cls.user = new_test_user(cls.env, login="cmc_lc_user", groups="cmc_base.group_cmc_user")

    def _computed(self):
        run = self.env["cmc.closing.run"].create({**self.period, "method": "direct"})
        run.action_compute()
        return run

    def test_compute_marks_done(self):
        run = self._computed()
        self.assertEqual(run.state, "done")
        self.assertTrue(run.line_ids)
        self.assertTrue(run.cost_analysis_ids)

    def test_header_input_locked_when_done(self):
        run = self._computed()
        for vals in ({"method": "step_down"}, {"date_from": date(date.today().year, 2, 1)}):
            with self.assertRaises(UserError):
                run.write(vals)

    def test_lock_holds_for_superuser(self):
        run = self._computed()
        with self.assertRaises(UserError):
            run.sudo().write({"method": "reciprocal"})

    def test_name_stays_editable(self):
        run = self._computed()
        run.write({"name": "Renamed"})
        self.assertEqual(run.name, "Renamed")

    def test_state_not_writable_directly(self):
        run = self._computed()
        with self.assertRaises(UserError):
            run.sudo().write({"state": "draft"})
        draft = self.env["cmc.closing.run"].create({**self.period})
        with self.assertRaises(UserError):
            draft.write({"state": "done"})
        with self.assertRaises(UserError):
            self.env["cmc.closing.run"].create({**self.period, "state": "done"})

    def test_sentinel_opens_state(self):
        draft = self.env["cmc.closing.run"].create({**self.period})
        draft.with_context(cmc_run_state=CMC_RUN_STATE).write({"state": "done"})
        self.assertEqual(draft.state, "done")

    def test_recompute_requires_reset(self):
        run = self._computed()
        with self.assertRaises(UserError):
            run.action_compute()

    def test_reset_deletes_results_and_unlocks(self):
        run = self._computed()
        run.with_user(self.user).action_reset_to_draft()
        self.assertEqual(run.state, "draft")
        self.assertFalse(run.line_ids)
        self.assertFalse(run.cost_analysis_ids)
        run.write({"method": "step_down"})
        run.action_compute()
        self.assertEqual(run.state, "done")

    def test_copy_is_draft(self):
        run = self._computed()
        self.assertEqual(run.copy().state, "draft")
