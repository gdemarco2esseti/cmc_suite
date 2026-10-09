"""WP-11 / AD-115 - scenario, simulation and consolidation runs as real CMC users, inside their company; results
are engine rows. CONTRACT: see common.py (ACL and record rules)."""

from odoo.exceptions import AccessError, UserError
from odoo.tests.common import new_test_user, tagged

from .common import PRICE, ScenarioCase


@tagged("post_install", "-at_install")
class TestSecurity(ScenarioCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company_b = cls.env["res.company"].create({"name": "CMC WP11 Co B"})
        cls.user_b = new_test_user(cls.env, login="cmc_wp11_user_b", groups="base.group_user,cmc_base.group_cmc_user",
                                   company_id=cls.company_b.id, company_ids=[(6, 0, cls.company_b.ids)])

    def _price_var(self):
        return [(self._path(self.center_a, PRICE), "normal", {"mean": 10.0, "stdev": 1.0})]

    def test_cmc_user_builds_and_compares_scenario(self):
        # FCS-02: the controller copies the approved baseline, changes a price and reads the delta P&L
        scenario = self._scenario(user=self.user)
        self.assertEqual(scenario.env.uid, self.user.id)
        self._set_input(scenario.with_user(self.user), self.center_a, PRICE, 11.0)
        comparison = self._comparison(scenario, user=self.user)
        comparison.action_compute()
        self.assertEqual(comparison.delta_profit, 1000.0)
        self.assertEqual(len(comparison.line_ids), 6)

    def test_cmc_user_runs_simulation(self):
        # RSK-01/02: a CMC user runs Monte Carlo, the engine writes the result rows
        sim = self._simulation(self._price_var(), user=self.user)
        sim.action_compute()
        self.assertEqual(sim.state, "done")
        self.assertTrue(sim.bin_ids and sim.tornado_ids and sim.sensitivity_ids)

    def test_cmc_user_consolidates(self):
        # INT-04: a CMC user consolidates approved versions
        run = self._consolidation(self.baseline, self._approved_child(), user=self.user)
        run.action_compute()
        self.assertEqual(run.total_amount, 1300.0)

    def _approved_child(self):
        child = self._version("WP11 sec child", {self.center_a: (10.0, 1000.0, 6.0, 3000.0), self.center_b: (20.0, 100.0, 12.0, 500.0)})
        child.action_approve()
        return child

    def test_user_cannot_write_results(self):
        # AD-001: results are read, never typed
        sim = self._simulation(self._price_var(), user=self.user)
        sim.action_compute()
        with self.assertRaises(AccessError):
            self.env["cmc.scenario.bin"].with_user(self.user).create({"simulation_id": sim.id, "count": 1})
        with self.assertRaises(AccessError):
            sim.bin_ids[:1].with_user(self.user).write({"count": 999})
        with self.assertRaises(AccessError):
            sim.sensitivity_ids.with_user(self.user).unlink()
        comparison = self._comparison(self._scenario(), user=self.user)
        comparison.action_compute()
        with self.assertRaises(AccessError):
            comparison.line_ids[:1].with_user(self.user).write({"delta": 1.0})

    def test_user_cannot_write_header_outputs(self):
        # AD-113: the statistics are computed, not typed
        sim = self._simulation(self._price_var(), user=self.user)
        with self.assertRaises(UserError):
            sim.mean_output = 99.0
        comparison = self._comparison(self._scenario(), user=self.user)
        with self.assertRaises(UserError):
            comparison.delta_profit = 99.0

    def test_user_cannot_delete_manager_can(self):
        # AD-115: deleting a run is a manager's call
        sim = self._simulation(self._price_var(), user=self.user)
        comparison = self._comparison(self._scenario(), user=self.user)
        with self.assertRaises(AccessError):
            sim.unlink()
        with self.assertRaises(AccessError):
            comparison.unlink()
        sim.with_user(self.manager).unlink()
        comparison.with_user(self.manager).unlink()
        self.assertFalse(sim.exists() or comparison.exists())

    def test_plain_user_has_no_access(self):
        # AD-115: scenarios and risk figures are CMC data
        sim = self._simulation(self._price_var())
        for model in ("cmc.scenario.simulation", "cmc.scenario.comparison", "cmc.scenario.consolidation", "cmc.scenario.variable"):
            with self.subTest(model=model), self.assertRaises(AccessError):
                self.env[model].with_user(self.outsider).search([])
        with self.assertRaises(AccessError):
            sim.with_user(self.outsider).action_compute()

    def test_other_company_user_sees_nothing(self):
        # AD-115: the company rule hides company A runs and results from a company B user
        sim = self._simulation(self._price_var())
        sim.action_compute()
        comparison = self._comparison(self._scenario())
        comparison.action_compute()
        for model in ("cmc.scenario.simulation", "cmc.scenario.bin", "cmc.scenario.sensitivity", "cmc.scenario.comparison", "cmc.scenario.comparison.line"):
            with self.subTest(model=model):
                self.assertEqual(self.env[model].with_user(self.user_b).search_count([]), 0)

    def test_company_rules_are_global(self):
        # AD-115: one global company rule per model, outside noupdate
        for model in ("cmc.scenario.simulation", "cmc.scenario.comparison", "cmc.scenario.consolidation",
                      "cmc.scenario.bin", "cmc.scenario.tornado", "cmc.scenario.sensitivity", "cmc.scenario.variable",
                      "cmc.scenario.comparison.line", "cmc.scenario.consolidation.line"):
            with self.subTest(model=model):
                rules = self.env["ir.rule"].search([("model_id.model", "=", model)])
                self.assertTrue(any(rule["global"] and "company_ids" in rule.domain_force for rule in rules))

    def test_cross_company_version_refused(self):
        # AD-115: a company B simulation cannot read a company A version
        with self.assertRaises(UserError):
            self._simulation(self._price_var(), company_id=self.company_b.id)
        with self.assertRaises(UserError):
            self._comparison(self._scenario(), company_id=self.company_b.id)
