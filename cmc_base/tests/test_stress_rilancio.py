"""Stress test 2026-10 (rilancio) - the cockpit chart for a role that reads costs but not the cost elements.

``cmc.cockpit.get_cockpit_data``: the cost-by-element chart is built only when the caller can read both
``account.analytic.line`` and ``cmc.cost.element``; otherwise it is empty (no AccessError on the element names).
The ``cmc_base.group_cmc_responsible`` role alone cannot read analytic lines, so the case is simulated with a
responsible that also holds ``analytic.group_analytic_accounting`` (reads analytic lines, not cost elements).
"""

from datetime import date

from odoo.tests.common import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestCockpitStressRilancio(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        plan = cls.env["account.analytic.plan"].search([("parent_id", "=", False)]).filtered(
            lambda p: p._column_name() == "account_id"
        )[:1]
        center = cls.env["account.analytic.account"].create(
            {"name": "RL cockpit center", "plan_id": plan.id, "cmc_center_type": "final", "company_id": cls.company.id}
        )
        cls.element = cls.env["cmc.cost.element"].create({"name": "RL cockpit element", "code": "RLCKPT", "company_id": cls.company.id})
        cls.env["account.analytic.line"].create({
            "name": "RL cockpit cost", "account_id": center.id, "amount": -500.0, "date": date.today(),
            "company_id": cls.company.id, "cmc_cost_element_id": cls.element.id, "cmc_behavior": "fixed",
        })
        cls.analytic_reader = new_test_user(
            cls.env, login="rl_cockpit_reader",
            groups="base.group_user,cmc_base.group_cmc_responsible,analytic.group_analytic_accounting",
        )
        cls.cmc_user = new_test_user(cls.env, login="rl_cockpit_user", groups="base.group_user,cmc_base.group_cmc_user")

    def test_reader_without_element_access_gets_no_chart(self):
        # FX cockpit: reading analytic lines but not cost elements -> no chart labels, no AccessError
        Cockpit = self.env["cmc.cockpit"].with_user(self.analytic_reader)
        self.assertTrue(self.env["account.analytic.line"].with_user(self.analytic_reader).has_access("read"))
        self.assertFalse(self.env["cmc.cost.element"].with_user(self.analytic_reader).has_access("read"))
        data = Cockpit.get_cockpit_data()
        # SC-082: no chart at all (the card is hidden), not an empty one claiming there is no classified cost
        self.assertFalse(data["chart"])

    def test_cmc_user_still_sees_element_in_chart(self):
        # FX cockpit regression: a role that reads the elements keeps the cost-by-element chart
        data = self.env["cmc.cockpit"].with_user(self.cmc_user).get_cockpit_data()
        self.assertIn(self.element.display_name, data["chart"]["labels"])
