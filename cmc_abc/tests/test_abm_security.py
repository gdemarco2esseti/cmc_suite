"""WP-08 / AD-115 - security and multi-company of the ABM models, exercised with the real roles.

CONTRACT (see abm_common)
- ``cmc.abm.initiative``: group_cmc_user read/write/create (improvement teams log their cases), manager full
  (unlink). ``cmc.abc.activity.measure``: user read, manager full (dictionary configuration, like the activity).
  ``cmc.abc.activity.performance``, ``cmc.abm.benchmark.line``: results (user read, engine writes).
  ``cmc.abm.benchmark``: user read/write/create, manager full. Non-CMC users: AccessError.
- Global rules ``company_id parent_of company_ids`` (benchmark: or company False) on every new model.
- A plain CMC user computes and reads the ABM figures of his company (same as the superuser).
"""

from odoo.exceptions import AccessError
from odoo.tests.common import new_test_user, tagged

from .abm_common import AbmCase


@tagged("post_install", "-at_install")
class TestAbmSecurity(AbmCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        a = cls.company_a
        cls.company_b = cls.env["res.company"].create({"name": "CMC WP08 sec B"})
        b = cls.company_b
        cls.user_a = new_test_user(cls.env, login="wp08_user_a", groups="base.group_user,cmc_base.group_cmc_user",
                                   company_id=a.id, company_ids=[(6, 0, a.ids)])
        cls.manager_a = new_test_user(cls.env, login="wp08_manager_a", groups="base.group_user,cmc_base.group_cmc_manager",
                                      company_id=a.id, company_ids=[(6, 0, a.ids)])
        cls.user_b = new_test_user(cls.env, login="wp08_user_b", groups="base.group_user,cmc_base.group_cmc_user",
                                   company_id=b.id, company_ids=[(6, 0, b.ids)])
        cls.outsider = new_test_user(cls.env, login="wp08_outsider", groups="base.group_user",
                                     company_id=a.id, company_ids=[(6, 0, a.ids)])
        cls.cycle = cls._driver(cls.env_a, "WP08SCYC", "duration", kind="statistical")

    def _env(self, user, company=None):
        company = company or self.company_a
        return self.env(user=user, context=dict(self.env.context, allowed_company_ids=company.ids))

    def _initiative_vals(self, baseline):
        return {"name": "user case", "activity_id": self.qc.id, "baseline_run_id": baseline.id,
                "expected_driver_reduction": 40.0, "one_time_cost": 6000.0}

    def test_cmc_user_logs_and_updates_an_initiative(self):
        # ABM-04: the improvement team (cmc user) creates and updates its business case
        env = self._env(self.user_a)
        baseline = self._main_run(env=env)
        initiative = env["cmc.abm.initiative"].create(self._initiative_vals(baseline))
        self.assertAlmostEqual(initiative.expected_annual_saving, 12000.0, places=2)
        review = self._main_run(env=env, resource_lines=self._lines_with(mach=58, qc=12), edges=self._edges_qc(2, 1))
        initiative.write({"review_run_id": review.id, "resource_action": "redeploy"})
        self.assertAlmostEqual(initiative.realized_annual_saving, 9600.0, places=2)
        self.assertEqual(initiative.benefit_status, "partial")

    def test_only_manager_deletes_initiatives(self):
        # ABM-04: a business case is evidence; only the manager removes it
        baseline = self._main_run()
        initiative = self.env_a["cmc.abm.initiative"].create(self._initiative_vals(baseline))
        with self.assertRaises(AccessError):
            initiative.with_user(self.user_a).unlink()
        initiative.with_user(self.manager_a).with_context(allowed_company_ids=self.company_a.ids).unlink()
        self.assertFalse(initiative.exists())

    def test_other_company_does_not_see_initiatives(self):
        # AD-115: initiatives of company A are invisible to company B
        initiative = self.env_a["cmc.abm.initiative"].create(self._initiative_vals(self._main_run()))
        self.assertFalse(self._env(self.user_b, self.company_b)["cmc.abm.initiative"].search([("id", "=", initiative.id)]))
        self.assertEqual(self._env(self.user_a)["cmc.abm.initiative"].search([("id", "=", initiative.id)]), initiative)

    def test_non_cmc_user_has_no_access(self):
        # AD-115: ABM data is management control, not for every employee
        env = self._env(self.outsider)
        for model in ("cmc.abm.initiative", "cmc.abc.activity.measure", "cmc.abc.activity.performance",
                      "cmc.abm.benchmark", "cmc.abm.benchmark.line"):
            with self.assertRaises(AccessError, msg=model):
                env[model].search([])

    def test_measure_lines_are_configured_by_the_manager(self):
        # ABM-03: measures are part of the activity dictionary: manager writes, user reads
        with self.assertRaises(AccessError):
            self._env(self.user_a)["cmc.abc.activity.measure"].create(
                {"activity_id": self.setup.id, "measure_id": self.cycle.id, "role": "cycle_time"}
            )
        line = self._env(self.manager_a)["cmc.abc.activity.measure"].create(
            {"activity_id": self.setup.id, "measure_id": self.cycle.id, "role": "cycle_time"}
        )
        self.assertEqual(self._env(self.user_a)["cmc.abc.activity.measure"].search([("id", "=", line.id)]), line)

    def test_cmc_user_computes_abm_figures(self):
        # AD-115: who presses Compute does not change the ABM figures
        self.env_a["cmc.abc.activity.measure"].create({"activity_id": self.setup.id, "measure_id": self.cycle.id, "role": "cycle_time"})
        env = self._env(self.user_a)
        run = self._main_run(env=env, defect_measure_id=self.defects.id)
        self.assertAlmostEqual(run._cmc_coq_split()["coq_total"], 7900.0, places=2)
        self.assertEqual([row["activity"] for row in run._cmc_activity_pareto()][:1], [self.mach.with_env(env)])
        self.assertAlmostEqual(run._cmc_object_cost_by_attribute("VA_CLASS")[self.obj_p1.id][self.nva.id], 3690.0, places=2)
        self.assertAlmostEqual(run.copq_total, 517.20, places=2)
        self.assertAlmostEqual(run.performance_ids.cost_per_output, 365.0, places=2)
