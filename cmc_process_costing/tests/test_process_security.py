"""WP-06 / AD-115 - roles act with their own user (with_user), two companies.

CONTRACT (part)
- A ``cmc_base.group_cmc_user`` (no MRP / accounting / inventory rights) creates, prefills and computes runs: the
  engine reads analytic lines, MRP facts and the closing in sudo inside the run company; figures identical to the
  superuser ones. Result lines are read-only for the user (AccessError on write).
- Without a CMC group: no run (AccessError on create and read).
- A CMC user of another company sees neither the runs nor their lines.
- Global company rules ``company_id parent_of company_ids`` on run and line, not ``noupdate``.
"""

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests.common import new_test_user, tagged

from .common import ProcessCase


@tagged("post_install", "-at_install")
class TestProcessSecurity(ProcessCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company_b = cls.env["res.company"].create({"name": "WP06 Company B"})
        cls.user_b = new_test_user(
            cls.env, login="wp06_user_b", groups="base.group_user,cmc_base.group_cmc_user",
            company_id=cls.company_b.id, company_ids=[Command.set(cls.company_b.ids)],
        )
        cls.outsider = new_test_user(
            cls.env, login="wp06_outsider", groups="base.group_user,mrp.group_mrp_user",
            company_id=cls.company.id, company_ids=[Command.set(cls.company.ids)],
        )

    def _env(self, user):
        return self.env(user=user)

    def test_cmc_user_computes_chained_runs(self):
        # FC-07 as the controller who presses Prefill and Compute
        d1 = self._d1(env=self._env(self.user))
        d2 = self._d2(d1, env=self._env(self.user))
        self.assertRecordValues(d1, [{"state": "done", "units_completed": 900.0, "units_spoiled": 100.0,
                                      "transferred_out_cost": 108900.0, "ending_wip_cost": 16100.0}])
        self.assertRecordValues(d2, [{"state": "done", "transferred_out_cost": 124800.0}])

    def test_cmc_user_resets_run(self):
        run = self._d1(env=self._env(self.user))
        run.action_reset_to_draft()
        self.assertEqual(run.state, "draft")

    def test_cmc_user_cannot_write_lines(self):
        run = self._d1(env=self._env(self.user))
        with self.assertRaises(AccessError):
            run.line_ids[:1].write({"ending_cost": 1.0})

    def test_outsider_cannot_create_run(self):
        with self.assertRaises(AccessError):
            self._run(env=self._env(self.outsider), prefill=False, compute=False)

    def test_outsider_cannot_read_runs(self):
        self._d1()
        with self.assertRaises(AccessError):
            self.env["cmc.process.run"].with_user(self.outsider).search([])

    def test_other_company_isolated(self):
        # AD-115: a CMC user of company B does not see the cost of production of company A
        run = self._d1()
        self.assertFalse(self.env["cmc.process.run"].with_user(self.user_b).search([("id", "in", run.ids)]))
        self.assertFalse(self.env["cmc.process.run.line"].with_user(self.user_b).search([("id", "in", run.line_ids.ids)]))

    def test_company_rules_not_noupdate(self):
        for model in ("cmc.process.run", "cmc.process.run.line"):
            rules = self.env["ir.rule"].search([("model_id.model", "=", model)])
            rule = rules.filtered(lambda r: r["global"] and "parent_of" in (r.domain_force or ""))
            self.assertTrue(rule, model)
            data = self.env["ir.model.data"].search([("model", "=", "ir.rule"), ("res_id", "in", rule.ids)])
            self.assertTrue(data)
            self.assertFalse(any(data.mapped("noupdate")), model)
