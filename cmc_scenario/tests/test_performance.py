"""WP-11 / AD-114 - the Compute cost does not grow with the trials, the variables or the lines.

CONTRACT
- Simulation: the evaluator is prepared once (one batched read of the version), trials run in memory (no query
  per trial, no row per trial), bins / tornado / sensitivity rows created with one ``create(vals_list)`` each.
  5,000 trials x 20 variables cost the same queries as 100 trials x 2 variables (small constant tolerance).
- Comparison / consolidation: one prepare per version, result lines created in one batch: 30 centers cost the
  same queries as 3.
"""

from odoo.tests.common import tagged

from .common import FIXED, PRICE, VARCOST, VOLUME, ScenarioCase

TOLERANCE = 5


@tagged("post_install", "-at_install")
class TestPerformance(ScenarioCase):
    def _assert_flat(self, small, big):
        self.env.flush_all()
        self.env.invalidate_all()
        start = self.cr.sql_log_count
        small.action_compute()
        self.env.flush_all()
        budget = self.cr.sql_log_count - start + TOLERANCE
        self.env.invalidate_all()
        with self.assertQueryCount(budget):
            big.action_compute()
            self.env.flush_all()

    def _wide_version(self, n, approve=False, **kw):
        centers = self.env["account.analytic.account"].create([
            {"name": "WP11 vol %s-%s" % (n, i), "plan_id": self.plan.id, "cmc_center_type": "final"} for i in range(n)
        ])
        version = self._version("WP11 wide %s" % n, {c: (10.0 + i, 100.0, 6.0, 50.0) for i, c in enumerate(centers)}, **kw)
        if approve:
            version.action_approve()
        return version, centers

    def _variables(self, centers, count):
        codes = (PRICE, VOLUME, VARCOST, FIXED)
        out = []
        for i in range(count):
            center, code = centers[i // 4], codes[i % 4]
            base = {PRICE: 10.0 + i // 4, VOLUME: 100.0, VARCOST: 6.0, FIXED: 50.0}[code]
            out.append((self._path(center, code), "uniform", {"low": base * 0.9, "high": base * 1.1}))
        return out

    def test_simulation_independent_of_trials(self):
        # RSK-01 / AD-114: 5,000 trials cost the same queries as 100 (trials never hit the DB)
        version, centers = self._wide_version(5)
        variables = self._variables(centers, 2)
        small = self._simulation(variables, version=version, trials=100)
        big = self._simulation(variables, version=version, trials=5000)
        self._assert_flat(small, big)
        self.assertEqual(sum(big.bin_ids.mapped("count")), 5000)

    def test_simulation_independent_of_variables(self):
        # RSK-01/02 / AD-114: 20 uncertain inputs cost the same queries as 2 (batched result rows)
        version, centers = self._wide_version(5)
        small = self._simulation(self._variables(centers, 2), version=version, trials=200)
        big = self._simulation(self._variables(centers, 20), version=version, trials=200)
        self._assert_flat(small, big)
        self.assertEqual((len(big.tornado_ids), len(big.sensitivity_ids)), (20, 20))

    def test_comparison_independent_of_lines(self):
        # FCS-02 / AD-114: 90 delta lines cost the same queries as 9
        small_base, _c = self._wide_version(3)
        big_base, _c = self._wide_version(30)
        small = self._comparison(small_base._cmc_scenario_copy())
        big = self._comparison(big_base._cmc_scenario_copy())
        self._assert_flat(small, big)
        self.assertEqual(len(big.line_ids), 90)

    def test_consolidation_independent_of_lines(self):
        # INT-04 / AD-114: two children of 30 centers cost the same queries as two of 3
        small_children = self._wide_version(3, approve=True)[0] | self._wide_version(3, approve=True)[0]
        big_children = self._wide_version(30, approve=True)[0] | self._wide_version(30, approve=True)[0]
        small = self._consolidation(self.baseline, small_children)
        big = self._consolidation(self.baseline, big_children)
        self._assert_flat(small, big)
        self.assertEqual(len(big.line_ids), 180)
