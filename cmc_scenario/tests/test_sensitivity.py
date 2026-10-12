"""WP-11 RSK-02 - tornado one-at-a-time +/-10% and Spearman rank correlation with significance rules (trust
abs(rho) >= 0.05, dominant > 0.10), re-run without the dominant variables. CONTRACT: see common.py.

Tornado hand case (base profit 1,300; center A price 10, volume 1,000, var 6, fixed 3,000):
  price 9 / 11 -> 300 / 2,300 (swing 2,000); var 5.4 / 6.6 -> 1,900 / 700 (1,200);
  volume 900 / 1,100 -> 900 / 1,700 (800); fixed 2,700 / 3,300 -> 1,600 / 1,000 (600).
Rank correlation case: price A normal(10, 1) (sd of the profit 1,000), var A uniform(5.5, 6.5) (289), fixed A
uniform(2,995, 3,005) (2.9): rho ~ +0.96 / -0.27 / ~0 at N = 10,000.
"""

from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .common import FIXED, PRICE, VARCOST, VOLUME, ScenarioCase


@tagged("post_install", "-at_install")
class TestSensitivity(ScenarioCase):
    def _tornado_sim(self, **kw):
        a = self.center_a
        return self._simulation([
            (self._path(a, FIXED), "normal", {"mean": 3000.0, "stdev": 10.0}),
            (self._path(a, VOLUME), "normal", {"mean": 1000.0, "stdev": 10.0}),
            (self._path(a, PRICE), "normal", {"mean": 10.0, "stdev": 0.1}),
            (self._path(a, VARCOST), "normal", {"mean": 6.0, "stdev": 0.1}),
        ], trials=200, **kw)

    def _correlation_sim(self, **kw):
        a = self.center_a
        return self._simulation([
            (self._path(a, PRICE), "normal", {"mean": 10.0, "stdev": 1.0}),
            (self._path(a, VARCOST), "uniform", {"low": 5.5, "high": 6.5}),
            (self._path(a, FIXED), "uniform", {"low": 2995.0, "high": 3005.0}),
        ], trials=10000, **kw)

    # ---- tornado -----------------------------------------------------------------------

    def test_tornado_swings_sorted(self):
        # RSK-02: deterministic +/-10% one at a time from the version inputs, widest bar first
        sim = self._tornado_sim()
        sim.action_compute()
        a = self.center_a
        rows = sim.tornado_ids.sorted("rank")
        self.assertRecordValues(rows, [
            {"input_path": self._path(a, PRICE), "base_value": 10.0, "output_low": 300.0, "output_high": 2300.0, "swing": 2000.0, "rank": 1},
            {"input_path": self._path(a, VARCOST), "base_value": 6.0, "output_low": 1900.0, "output_high": 700.0, "swing": 1200.0, "rank": 2},
            {"input_path": self._path(a, VOLUME), "base_value": 1000.0, "output_low": 900.0, "output_high": 1700.0, "swing": 800.0, "rank": 3},
            {"input_path": self._path(a, FIXED), "base_value": 3000.0, "output_low": 1600.0, "output_high": 1000.0, "swing": 600.0, "rank": 4},
        ])
        self.assertEqual(rows[0].variable_id.input_path, self._path(a, PRICE))

    def test_tornado_swing_is_configurable(self):
        # RSK-02: +/-20% doubles the swings of a linear model
        sim = self._tornado_sim(swing_pct=20.0)
        sim.action_compute()
        top = sim.tornado_ids.sorted("rank")[0]
        self.assertRecordValues(top, [{"output_low": -700.0, "output_high": 3300.0, "swing": 4000.0}])

    # ---- rank correlation --------------------------------------------------------------

    def test_rank_correlation_and_flags(self):
        # RSK-02: sign and size of rho per input; trust abs(rho) >= 0.05, dominant > 0.10
        sim = self._correlation_sim()
        sim.action_compute()
        a = self.center_a
        rows = {row.input_path: row for row in sim.sensitivity_ids}
        price, var, fixed = rows[self._path(a, PRICE)], rows[self._path(a, VARCOST)], rows[self._path(a, FIXED)]
        self.assertGreater(price.rank_correlation, 0.9)
        self.assertTrue(-0.35 < var.rank_correlation < -0.18, var.rank_correlation)
        self.assertLess(abs(fixed.rank_correlation), 0.05)
        self.assertRecordValues(price | var | fixed, [
            {"is_significant": True, "is_dominant": True, "rank": 1},
            {"is_significant": True, "is_dominant": True, "rank": 2},
            {"is_significant": False, "is_dominant": False, "rank": 3},
        ])

    def test_thresholds_are_configurable(self):
        # RSK-02: the significance rules are parameters of the run
        sim = self._correlation_sim(significance_threshold=0.3, dominant_threshold=0.5)
        sim.action_compute()
        var = sim.sensitivity_ids.filtered(lambda r: r.input_path == self._path(self.center_a, VARCOST))
        self.assertRecordValues(var, [{"is_significant": False, "is_dominant": False}])

    def test_rerun_without_dominant_exposes_masked_variable(self):
        # RSK-02 / LCC Ch7: remove the dominant inputs and re-run; the masked fixed cost becomes visible
        sim = self._correlation_sim()
        sim.action_compute()
        rerun = sim._cmc_rerun_without_dominant()
        self.assertRecordValues(rerun, [{
            "state": "draft", "parent_simulation_id": sim.id, "version_id": sim.version_id.id, "trials": 10000,
            "seed": sim.seed, "sampling": sim.sampling, "evaluator_key": sim.evaluator_key,
        }])
        self.assertEqual(rerun.variable_ids.mapped("input_path"), [self._path(self.center_a, FIXED)])
        rerun.action_compute()
        self.assertRecordValues(rerun.sensitivity_ids, [{"is_significant": True, "rank": 1}])
        self.assertLess(rerun.sensitivity_ids.rank_correlation, -0.99)
        # dominant inputs stay at their base value: the profit centre stays 1,300
        self.assertAlmostEqual(rerun.mean_output, 1300.0, delta=1)

    def test_rerun_action_opens_new_run(self):
        sim = self._correlation_sim()
        sim.action_compute()
        action = sim.action_rerun_without_dominant()
        self.assertEqual(action["res_model"], "cmc.scenario.simulation")
        self.assertEqual(self.env["cmc.scenario.simulation"].browse(action["res_id"]).parent_simulation_id, sim)

    def test_rerun_refused_without_purpose(self):
        # RSK-02: nothing to remove, nothing left, or not computed -> UserError
        draft = self._correlation_sim()
        with self.assertRaises(UserError):
            draft._cmc_rerun_without_dominant()
        none_dominant = self._correlation_sim(dominant_threshold=1.0)
        none_dominant.action_compute()
        with self.assertRaises(UserError):
            none_dominant._cmc_rerun_without_dominant()
        only_dominant = self._simulation([(self._path(self.center_a, PRICE), "normal", {"mean": 10.0, "stdev": 1.0})])
        only_dominant.action_compute()
        with self.assertRaises(UserError):
            only_dominant._cmc_rerun_without_dominant()
