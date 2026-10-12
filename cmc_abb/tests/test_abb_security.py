"""WP-12 / AD-115 - security and multi-company of the ``cmc_abb`` models.

CONTRACT (see common.py for the whole WP)
- ACL: ``cmc.abb.run``, ``cmc.abb.variance.run`` and the input lines (``cmc.abb.bill.line``,
  ``cmc.abb.usage.line``, ``cmc.abb.driver.override``, ``cmc.abb.time.state``): group_cmc_user
  read/write/create/unlink; results (``cmc.abb.activity.result``, ``cmc.abb.resource.result``,
  ``cmc.abb.object.result``, ``cmc.abb.capacity.state``, ``cmc.abb.warning``, ``cmc.abb.variance.line``):
  user READ; manager full; base.group_user alone: nothing (AccessError).
- Spending profile fields on ``cmc.abc.resource`` are configuration: user reads, manager writes.
- Global rules ``company_id parent_of company_ids`` on every new model; ``_check_company_auto``.
- A CMC user computes the ABB (writes the target budget lines) and the variance with the superuser's figures.
"""

from odoo.exceptions import AccessError, UserError
from odoo.tests.common import new_test_user, tagged

from .common import AbbCase

NEW_MODELS = (
    "cmc.abb.run", "cmc.abb.bill.line", "cmc.abb.usage.line", "cmc.abb.driver.override", "cmc.abb.time.state",
    "cmc.abb.activity.result", "cmc.abb.resource.result", "cmc.abb.object.result", "cmc.abb.capacity.state",
    "cmc.abb.warning", "cmc.abb.variance.run", "cmc.abb.variance.line",
)


@tagged("post_install", "-at_install")
class TestAbbSecurity(AbbCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        a = cls.company_a
        cls.company_b = b = cls.env["res.company"].create({"name": "CMC WP12 B"})
        groups_user = "base.group_user,cmc_base.group_cmc_user"
        cls.user_a = new_test_user(cls.env, login="wp12_user_a", groups=groups_user, company_id=a.id, company_ids=[(6, 0, a.ids)])
        cls.user_b = new_test_user(cls.env, login="wp12_user_b", groups=groups_user, company_id=b.id, company_ids=[(6, 0, b.ids)])
        cls.outsider = new_test_user(cls.env, login="wp12_outsider", groups="base.group_user",
                                     company_id=a.id, company_ids=[(6, 0, a.ids)])
        env_b = cls._company_env(b)
        cls.units_b = cls._driver(env_b, "WP12UNITSB", "transaction", kind="statistical", company=b)
        cls.fc27_b = cls._version(env_b, "WP12 FC27 B", "forecast", company=b)
        obj_b = env_b["cmc.cost.object"]._cmc_ensure_objects(cls.p1, company=b)
        cls._fact(env_b, cls.units_b, 10, cost_object=obj_b, day=cls.fc27_b.date_from, version=cls.fc27_b)

    def _env(self, user, company=None):
        company = company or self.company_a
        return self.env(user=user, context=dict(self.env.context, allowed_company_ids=company.ids))

    def test_cmc_user_computes_the_budget(self):
        # AD-115: who presses Compute does not change the budget; the user writes the target lines
        run = self._abb_run(env=self._env(self.user_a))
        self.assertEqual(run.state, "done")
        self.assertAlmostEqual(run.total_budgeted_cost, 178800.0, places=2)
        self.assertAlmostEqual(self._act(run, self.mach).rate, 47.0, places=4)
        lines = self._env(self.user_a)["cmc.budget.line"].search([("abb_run_id", "=", run.id)])
        self.assertEqual(len(lines), 48)
        self.assertEqual(lines.mapped("create_uid"), self.user_a)

    def test_cmc_user_runs_what_if_and_variance(self):
        # AD-115: scenario and variance are user work, not configuration
        env = self._env(self.user_a)
        run = self._abb_run(env=env)
        whatif = run.action_what_if()
        whatif.driver_override_ids = [(0, 0, {"activity_id": self.ship.id, "quantity": 1700})]
        whatif.action_compute()
        self.assertAlmostEqual(whatif.delta_cost, 25000.0, places=2)
        variance = env["cmc.abb.variance.run"].create({
            "name": "WP12 user variance", "company_id": self.company_a.id, "abb_run_id": run.id,
            "date_from": run.source_version_id.date_from, "date_to": run.source_version_id.date_to,
        })
        variance.action_compute()
        self.assertEqual(len(variance.line_ids), 4)

    def test_cmc_user_cannot_configure_spending_profile(self):
        # AD-115: spending profiles and unit costs are configuration (manager)
        tech = self._env(self.user_a)["cmc.abc.resource"].browse(self.tech.id)
        self.assertEqual(tech.spending_profile, "committed_step")
        with self.assertRaises(AccessError):
            tech.unit_cost = 1
        self._env(self.manager_a)["cmc.abc.resource"].browse(self.tech.id).unit_cost = 50000
        self.assertEqual(self.tech.unit_cost, 50000)

    def test_cmc_user_cannot_write_results(self):
        # AD-115: results are read-only for users
        run = self._abb_run(env=self._env(self.user_a))
        with self.assertRaises(AccessError):
            run.resource_result_ids[0].budgeted_cost = 1
        with self.assertRaises(AccessError):
            run.activity_result_ids[0].unlink()

    def test_outsider_has_no_access(self):
        # AD-115: a non-controlling employee sees nothing of the ABB model
        self._abb_run()
        env = self._env(self.outsider)
        for model in NEW_MODELS:
            with self.assertRaises(AccessError, msg=model):
                env[model].search([])

    def test_other_company_user_sees_nothing_of_a(self):
        # AD-115: company rule on every new model
        run = self._abb_run()
        env = self._env(self.user_b, self.company_b)
        self.assertFalse(env["cmc.abb.run"].search([("id", "=", run.id)]))
        for model in ("cmc.abb.resource.result", "cmc.abb.activity.result", "cmc.abb.bill.line", "cmc.abb.capacity.state"):
            self.assertFalse(env[model].search([("run_id", "=", run.id)]), model)

    def test_version_of_other_company_rejected(self):
        # _check_company_auto: a company-A budget cannot read a company-B forecast
        with self.assertRaises(UserError):
            self._abb_run(source_version_id=self.fc27_b.id)

    def test_rules_are_global_parent_of(self):
        # AD-115: one global company rule per new model, not noupdate
        for model in NEW_MODELS:
            rules = self.env["ir.rule"].search([("model_id.model", "=", model), ("global", "=", True)])
            self.assertTrue(rules.filtered(lambda r: "parent_of" in (r.domain_force or "")), model)
