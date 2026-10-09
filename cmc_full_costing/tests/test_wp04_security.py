"""WP-04 - the new engine paths run as a real CMC user, not only as superuser (with_user).

CONTRACT
- A ``cmc_base.group_cmc_user`` of company A creates and computes an ``actual`` / ``practical`` costing
  run: the engine reads analytic lines, measure facts, cost objects and capacity analyses in sudo inside
  the run company perimeter, publishes rates and records usages; results are identical to the
  superuser ones and the user can READ the published rates of its company.
- The user cannot write the new result fields (results are engine-only, read-only ACL -> AccessError).
- A user without a CMC group cannot create a costing run (AccessError).
- A CMC user of company B sees neither the results nor the rates of company A (record rules).
"""

from odoo.exceptions import AccessError
from odoo.tests.common import new_test_user, tagged

from .wp04_common import Wp04Case


@tagged("post_install", "-at_install")
class TestWp04Security(Wp04Case):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user_a = new_test_user(
            cls.env, login="wp04_cmc_a", groups="base.group_user,cmc_base.group_cmc_user",
            company_id=cls.company_a.id, company_ids=[(6, 0, cls.company_a.ids)],
        )
        cls.user_b = new_test_user(
            cls.env, login="wp04_cmc_b", groups="base.group_user,cmc_base.group_cmc_user",
            company_id=cls.company_b.id, company_ids=[(6, 0, cls.company_b.ids)],
        )
        cls.outsider = new_test_user(
            cls.env, login="wp04_outsider", groups="base.group_user",
            company_id=cls.company_a.id, company_ids=[(6, 0, cls.company_a.ids)],
        )

    def _user_env(self, user, company):
        return self.env(user=user, context=dict(self.env.context, allowed_company_ids=company.ids))

    def test_cmc_user_computes_actual_run(self):
        # FC-04/05 as the role that presses Compute
        run = self._run_a(env=self._user_env(self.user_a, self.company_a))
        self.assertEqual(run.state, "done")
        self.assertRecordValues(
            self._res(run, self.p1),
            [{"cost_primo": 420.0, "produced_qty": 100.0, "unit_cost_industriale": 14.40, "cost_variable": 540.0}],
        )
        rates = run.env["cmc.rate"].search([("origin_model", "=", "cmc.costing.run"), ("origin_id", "=", run.id)])
        self.assertEqual(len(rates), 6)

    def test_cmc_user_computes_practical_run(self):
        # CAP-03 as the CMC user: capacity analyses read inside the engine
        run = self._run_a(env=self._user_env(self.user_a, self.company_a), capacity_basis="practical")
        self.assertAlmostEqual(run.unabsorbed_cost, 200.0, places=2)
        self.assertAlmostEqual(self._res(run, self.p1).cost_indiretto, 900.0, places=2)

    def test_cmc_user_cannot_write_unit_cost(self):
        run = self._run_a(env=self._user_env(self.user_a, self.company_a))
        with self.assertRaises(AccessError):
            self._res(run, self.p1).write({"unit_cost_industriale": 1.0})

    def test_outsider_cannot_create_run(self):
        with self.assertRaises(AccessError):
            self._run_a(env=self._user_env(self.outsider, self.company_a), compute=False)

    def test_other_company_user_sees_nothing_of_company_a(self):
        run = self._run_a()
        self.assertTrue(self.env["cmc.rate"].search_count([("origin_model", "=", "cmc.costing.run"), ("origin_id", "=", run.id)]))
        env_b = self._user_env(self.user_b, self.company_b)
        self.assertFalse(env_b["cmc.rate"].search([("origin_model", "=", "cmc.costing.run"), ("origin_id", "=", run.id)]))
        self.assertFalse(env_b["cmc.costing.result"].search([("run_id", "=", run.id)]))
