"""WP-07 / AD-115 - security and multi-company of the ABC v2 models.

CONTRACT (see abc2_common for the whole WP)
- ACL: ``cmc.abc.resource``, ``cmc.abc.driver.component``: group_cmc_user read, group_cmc_manager full.
  ``cmc.abc.resource.line``, ``cmc.abc.capacity`` (run inputs): user read/write/create/unlink.
  ``cmc.abc.activity.cost``, ``cmc.abc.flow`` (results): user read (+ create/unlink only through the
  engine sentinel), manager full. Non-CMC users (base.group_user): no access (AccessError).
- Global rules ``company_id parent_of company_ids`` on every new model (not noupdate), ``_check_company_auto``.
- A plain CMC user computes the whole network and gets the superuser's figures (pools and facts read in
  sudo inside the run company perimeter, AD-115); the other company's lines never enter.
"""

from odoo.exceptions import AccessError, UserError
from odoo.tests.common import new_test_user, tagged

from .abc2_common import Abc2Case


@tagged("post_install", "-at_install")
class TestAbc2Security(Abc2Case):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        a, b = cls.company_a, cls.env["res.company"].create({"name": "CMC WP07 B"})
        cls.company_b = b
        groups_user = "base.group_user,cmc_base.group_cmc_user"
        cls.user_a = new_test_user(cls.env, login="wp07_user_a", groups=groups_user,
                                   company_id=a.id, company_ids=[(6, 0, a.ids)])
        cls.manager_a = new_test_user(cls.env, login="wp07_manager_a", groups="base.group_user,cmc_base.group_cmc_manager",
                                      company_id=a.id, company_ids=[(6, 0, a.ids)])
        cls.user_b = new_test_user(cls.env, login="wp07_user_b", groups=groups_user,
                                   company_id=b.id, company_ids=[(6, 0, b.ids)])
        cls.outsider = new_test_user(cls.env, login="wp07_outsider", groups="base.group_user",
                                     company_id=a.id, company_ids=[(6, 0, a.ids)])
        env_b = cls._company_env(b)
        cls.zb = cls._center(env_b, "WP07 ZB", b)
        cls._cost(env_b, cls.zb, 5555, cls.mp, "fixed")
        cls.rb = env_b["cmc.abc.resource"].create({"name": "ZB all", "code": "WP07RB", "center_id": cls.zb.id})

    def _env(self, user, company=None):
        company = company or self.company_a
        return self.env(user=user, context=dict(self.env.context, allowed_company_ids=company.ids))

    def test_cmc_user_computes_the_network(self):
        # AD-115: who presses Compute does not change the certified figures
        run = self._main_run(env=self._env(self.user_a))
        self.assertEqual(run.state, "done")
        self.assertAlmostEqual(run.total_resource_cost, 16500.0, places=2)  # company B 5555 stays out
        self.assertAlmostEqual(self._res(run, self.obj_p1).amount, 7758.0, places=2)
        self.assertAlmostEqual(run.total_object_cost, 13930.0, places=2)
        self.assertAlmostEqual(run.sustaining_cost, 2000.0, places=2)
        self.assertTrue(run.flow_ids)

    def test_cmc_user_cannot_configure_resources(self):
        # AD-115: the resource layer is configuration (manager); users read it
        env = self._env(self.user_a)
        self.assertEqual(env["cmc.abc.resource"].search([("code", "=", "WP07R1")]), self.r1)
        with self.assertRaises(AccessError):
            env["cmc.abc.resource"].create({"name": "user res", "code": "WP07RU", "center_id": self.s.id,
                                            "cost_element_ids": [(6, 0, self.mp.ids)]})
        menv = self._env(self.manager_a)
        res = menv["cmc.abc.resource"].create({"name": "mgr res", "code": "WP07RM", "center_id": self.s.id,
                                               "cost_element_ids": [(6, 0, self.mp.ids)]})
        self.assertTrue(res)

    def test_cmc_user_cannot_write_results(self):
        # AD-115: results are read-only for users
        run = self._main_run(env=self._env(self.user_a))
        with self.assertRaises(UserError):
            run.flow_ids[0].amount = 1.0

    def test_outsider_has_no_access(self):
        # AD-115: a non-controlling employee sees nothing of the ABC model
        run = self._main_run()
        env = self._env(self.outsider)
        for model in ("cmc.abc.resource", "cmc.abc.activity.cost", "cmc.abc.flow", "cmc.abc.resource.line"):
            with self.assertRaises(AccessError, msg=model):
                env[model].search([])
        with self.assertRaises(AccessError):
            env["cmc.abc.run"].browse(run.id).read(["total_object_cost"])

    def test_other_company_user_sees_nothing_of_a(self):
        # AD-115: company rule on every new model
        run = self._main_run()
        env = self._env(self.user_b, self.company_b)
        self.assertFalse(env["cmc.abc.resource"].search([("id", "in", (self.r1 | self.r2 | self.r3).ids)]))
        self.assertFalse(env["cmc.abc.flow"].search([("run_id", "=", run.id)]))
        self.assertFalse(env["cmc.abc.activity.cost"].search([("run_id", "=", run.id)]))
        self.assertEqual(env["cmc.abc.resource"].search([("code", "=", "WP07RB")]), self.rb)

    def test_resource_of_other_company_rejected(self):
        # _check_company_auto: a company-A run cannot pool a company-B resource
        run = self._main_run(compute=False)
        with self.assertRaises(UserError):
            self.env["cmc.abc.resource.line"].create(
                {"run_id": run.id, "resource_id": self.rb.id, "activity_id": self.qc.id, "percent": 100}
            )
