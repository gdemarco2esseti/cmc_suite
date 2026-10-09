"""WP-11 RSK-01 - Monte Carlo / Latin Hypercube on version inputs through the evaluator: statistics, percentiles,
P(profit > 0), histogram bins (no stored samples). CONTRACT: see common.py.

Hand case: only price A uncertain -> profit = 1,000 x price - 8,700.
  normal(10, 1): mean 1,300, sd 1,000, P5 -344.85, P95 2,944.85, P(profit > 0) = Phi(1.3) = 90.32%.
  triangular(8, 10, 12): mean 1,300, sd 816.50; uniform(9, 11): mean 1,300, sd 577.35, range 300..2,300.
"""

from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import tagged

from .common import PRICE, VOLUME, ScenarioCase


@tagged("post_install", "-at_install")
class TestSimulation(ScenarioCase):
    def _price_a(self, dist="normal", **params):
        return (self._path(self.center_a, PRICE), dist, params or {"mean": 10.0, "stdev": 1.0})

    def test_normal_lhs_statistics(self):
        # RSK-01: forecast statistics and percentiles of the profit, LHS converges tightly at N = 2,000
        sim = self._simulation([self._price_a()])
        sim.action_compute()
        self.assertEqual(sim.state, "done")
        self.assertEqual(sim.base_output, 1300.0)
        self.assertAlmostEqual(sim.mean_output, 1300.0, delta=5)
        self.assertAlmostEqual(sim.stdev_output, 1000.0, delta=15)
        self.assertAlmostEqual(sim.p5_output, -344.85, delta=25)
        self.assertAlmostEqual(sim.p50_output, 1300.0, delta=10)
        self.assertAlmostEqual(sim.p95_output, 2944.85, delta=25)
        self.assertAlmostEqual(sim.prob_positive_pct, 90.32, delta=0.5)
        self.assertLess(sim.min_output, sim.p5_output)
        self.assertGreater(sim.max_output, sim.p95_output)

    def test_srs_within_sampling_error(self):
        # RSK-01: plain Monte Carlo, error ~ sd / sqrt(N) = 22 (tolerance ~4.5 sigma)
        sim = self._simulation([self._price_a()], sampling="srs")
        sim.action_compute()
        self.assertAlmostEqual(sim.mean_output, 1300.0, delta=100)
        self.assertAlmostEqual(sim.stdev_output, 1000.0, delta=80)

    def test_triangular_distribution(self):
        # RSK-01: triangular(8, 10, 12) -> sd 816.50, bounded outcome
        sim = self._simulation([self._price_a("triangular", low=8.0, mode=10.0, high=12.0)])
        sim.action_compute()
        self.assertAlmostEqual(sim.mean_output, 1300.0, delta=5)
        self.assertAlmostEqual(sim.stdev_output, 816.50, delta=15)
        self.assertGreaterEqual(sim.min_output, -700.0)
        self.assertLessEqual(sim.max_output, 3300.0)

    def test_uniform_distribution_always_profitable(self):
        # RSK-01: uniform(9, 11) -> profit in 300..2,300, P(profit > 0) = 100%
        sim = self._simulation([self._price_a("uniform", low=9.0, high=11.0)])
        sim.action_compute()
        self.assertAlmostEqual(sim.stdev_output, 577.35, delta=10)
        self.assertGreaterEqual(sim.min_output, 300.0)
        self.assertLessEqual(sim.max_output, 2300.0)
        self.assertEqual(sim.prob_positive_pct, 100.0)

    def test_two_independent_variables(self):
        # RSK-01: price A normal(10, 1) + price B uniform(18, 22) -> sd sqrt(1,000^2 + 115.47^2) = 1,006.65
        sim = self._simulation([
            self._price_a(),
            (self._path(self.center_b, PRICE), "uniform", {"low": 18.0, "high": 22.0}),
        ])
        sim.action_compute()
        self.assertAlmostEqual(sim.mean_output, 1300.0, delta=10)
        self.assertAlmostEqual(sim.stdev_output, 1006.65, delta=20)

    def test_output_in_company_currency(self):
        # RSK-01 / INT-07: a version in another currency is simulated in company currency
        scenario = self._scenario()
        scenario.currency_rate = 2.0
        sim = self._simulation([self._price_a("uniform", low=9.0, high=11.0)], version=scenario)
        sim.action_compute()
        self.assertEqual(sim.base_output, 2600.0)
        self.assertAlmostEqual(sim.mean_output, 2600.0, delta=10)

    def test_same_seed_same_result(self):
        # AD-114: a fixed seed per run makes the simulation reproducible
        first = self._simulation([self._price_a()], sampling="srs", seed=7)
        second = self._simulation([self._price_a()], sampling="srs", seed=7)
        (first | second).action_compute()
        fields = ["mean_output", "stdev_output", "p5_output", "p50_output", "p95_output", "prob_positive_pct", "min_output", "max_output"]
        self.assertEqual(first.read(fields)[0] | {"id": 0}, second.read(fields)[0] | {"id": 0})
        self.assertEqual(first.bin_ids.mapped("count"), second.bin_ids.mapped("count"))

    def test_other_seed_other_draw(self):
        # AD-114: the seed is really used
        first = self._simulation([self._price_a()], sampling="srs", seed=7)
        second = self._simulation([self._price_a()], sampling="srs", seed=8)
        (first | second).action_compute()
        self.assertNotEqual(first.mean_output, second.mean_output)

    def test_histogram_bins(self):
        # RSK-01: stored bins only (AD-114) covering min..max, counts re-sum to N
        sim = self._simulation([self._price_a()], bin_count=10)
        sim.action_compute()
        bins = sim.bin_ids.sorted("sequence")
        self.assertEqual(len(bins), 10)
        self.assertEqual(bins.mapped("sequence"), list(range(1, 11)))
        self.assertEqual(sum(bins.mapped("count")), 2000)
        self.assertAlmostEqual(sum(bins.mapped("frequency_pct")), 100.0, delta=0.1)
        self.assertAlmostEqual(bins[0].lower, sim.min_output, delta=0.01)
        self.assertAlmostEqual(bins[-1].upper, sim.max_output, delta=0.01)
        for left, right in zip(bins[:-1], bins[1:], strict=True):
            self.assertAlmostEqual(left.upper, right.lower, delta=0.01)

    def test_no_samples_stored(self):
        # AD-114: rows = bins + tornado + sensitivity, never one per trial
        sim = self._simulation([self._price_a(), (self._path(self.center_a, VOLUME), "uniform", {"low": 900.0, "high": 1100.0})], bin_count=5)
        sim.action_compute()
        self.assertEqual((len(sim.bin_ids), len(sim.tornado_ids), len(sim.sensitivity_ids)), (5, 2, 2))

    # ---- validation ------------------------------------------------------------------

    def test_trials_bounds(self):
        # AD-114: 1 <= N <= 10,000
        for trials in (0, 10001):
            with self.subTest(trials=trials), self.assertRaises(ValidationError):
                self._simulation([self._price_a()], trials=trials)

    def test_header_bounds(self):
        for kw in ({"bin_count": 0}, {"bin_count": 101}, {"swing_pct": 0.0}):
            with self.subTest(**kw), self.assertRaises(ValidationError):
                self._simulation([self._price_a()], **kw)

    def test_minimum_trials_per_variable(self):
        # RSK-01: N >= 6 x variables
        two = [self._price_a(), (self._path(self.center_a, VOLUME), "uniform", {"low": 900.0, "high": 1100.0})]
        with self.assertRaises(UserError):
            self._simulation(two, trials=11).action_compute()
        sim = self._simulation(two, trials=12)
        sim.action_compute()
        self.assertEqual(sim.state, "done")

    def test_invalid_distribution_params(self):
        # RSK-01: a mistyped distribution is refused when saved
        path = self._path(self.center_a, PRICE)
        for dist, params in (
            ("normal", {"mean": 10.0, "stdev": -1.0}),
            ("triangular", {"low": 8.0, "mode": 13.0, "high": 12.0}),
            ("triangular", {"low": 8.0, "mode": 8.0, "high": 8.0}),
            ("uniform", {"low": 11.0, "high": 11.0}),
        ):
            with self.subTest(dist=dist, params=params), self.assertRaises(ValidationError):
                self._simulation([(path, dist, params)])

    def test_same_input_twice_refused(self):
        # RSK-01: one distribution per input
        with self.assertRaises(ValidationError):
            self._simulation([self._price_a(), self._price_a("uniform", low=9.0, high=11.0)])

    def test_unknown_input_path_raises(self):
        # RSK-01: a variable must point at an input the evaluator reads
        sim = self._simulation([("999999/2026-01/%s" % PRICE, "uniform", {"low": 1.0, "high": 2.0})])
        with self.assertRaisesRegex(UserError, "999999/2026-01"):
            sim.action_compute()

    def test_no_variable_raises(self):
        with self.assertRaises(UserError):
            self._simulation([]).action_compute()

    # ---- lifecycle ---------------------------------------------------------------------

    def test_computed_inputs_locked_and_reset_clears(self):
        # AD-113: inputs frozen while computed; reset deletes bins/tornado/sensitivity and clears outputs
        sim = self._simulation([self._price_a()])
        sim.action_compute()
        with self.assertRaises(UserError):
            sim.variable_ids.stdev = 2.0
        with self.assertRaises(UserError):
            sim.trials = 500
        sim.action_reset_to_draft()
        self.assertRecordValues(sim, [{"state": "draft", "mean_output": 0.0, "p95_output": 0.0, "prob_positive_pct": 0.0, "base_output": 0.0}])
        self.assertFalse(sim.bin_ids or sim.tornado_ids or sim.sensitivity_ids)

    def test_evaluator_prepared_once_per_compute(self):
        # AD-114: trials never hit the DB - the version is read once, the evaluator runs in memory
        sim = self._simulation([self._price_a()], trials=3000)
        sim.action_compute()
        self.assertEqual(self.prepare_calls, [self.baseline.id])
