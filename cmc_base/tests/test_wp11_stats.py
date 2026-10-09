"""WP-11 / AD-119 - sampling, Latin Hypercube, percentiles, rank correlation as pure stdlib functions in
``cmc_utils``; AD-102 - the scenario evaluator registry lives on ``cmc.plan.version`` in ``cmc_base``.

Sources: gap register RSK-01 (Monte Carlo / LHS, normal / triangular / uniform, percentiles, P(profit > 0),
error ~ 1/sqrt(N)), RSK-02 (rank correlation, trust abs(rho) >= 0.05, dominant > 0.10); Emblemsvag LCC Ch3, Ch6-8,
App A (LHS partitions each variable range into N equal-probability intervals; mean, median, sd, percentiles).

CONTRACT (``odoo.addons.cmc_base.models.cmc_utils``, Python stdlib only: ``math``, ``random``, ``statistics``)
- Distributions and their params (dict): ``"normal"`` {mean, stdev} (stdev >= 0), ``"triangular"`` {low, mode,
  high} (low <= mode <= high, low < high), ``"uniform"`` {low, high} (low < high). Invalid params or an unknown
  distribution -> ValueError.
- ``inverse_cdf(distribution, params, p)`` -> float, the quantile at probability p (0 < p < 1); normal through
  ``statistics.NormalDist``, triangular and uniform in closed form.
- ``draw(distribution, params, n, rng, lhs=False)`` -> list of n floats. ``rng`` is a ``random.Random`` owned by
  the caller (the global ``random`` state is never used: a seeded run is deterministic). SRS: inverse_cdf of
  rng.random(). LHS: stratum i of [0, 1) gets u = (i + rng.random()) / n, the strata are shuffled with ``rng``,
  then mapped through inverse_cdf (each of the n equal-probability intervals holds exactly one sample).
  n < 1 -> ValueError.
- ``percentile(values, q)`` -> float, q in [0, 100] (else ValueError), linear interpolation between closest
  ranks on the sorted values (position (n - 1) x q / 100, the "inclusive" method); empty -> ValueError.
- ``ranks(values)`` -> list of 1-based ranks, ties get the average rank.
- ``spearman(xs, ys)`` -> Pearson correlation of the average ranks, in [-1, 1]; 0.0 when either series is
  constant; different lengths or fewer than 2 points -> ValueError.
- ``histogram(values, bins)`` -> list of (lower, upper, count): ``bins`` equal-width bins from min to max, the
  last bin closed on the right; all values equal -> one bin (v, v, n); bins < 1 or empty values -> ValueError.
- ``describe(values)`` -> dict {mean, stdev (sample; 0.0 for one value), min, max, p5, p50, p95,
  prob_positive (share of values strictly > 0, a fraction 0..1)}.

CONTRACT (evaluator registry, ``cmc.plan.version`` in ``cmc_base``)
- ``_cmc_scenario_evaluators()`` -> list of dicts, base returns ``[]``; producers (cmc_budget lines,
  cmc_direct_costing CVR) extend it with ``super() + [...]``. Entry keys: ``key`` (unique str), ``name``,
  ``prepare`` = callable(version) -> {"inputs": {path: float}, "evaluate": callable(values) -> {(center_id |
  False, element_id | False, month date): amount}}. ``prepare`` does every DB read (batched) of one version;
  ``evaluate`` is PURE (no DB): values carries every input path, amounts are signed as contributions to the
  profit (revenue +, cost -) in the version currency, profit = sum of the amounts.
- ``_cmc_scenario_evaluator(key)`` -> the entry with that key; unknown key -> UserError naming it.
"""

import random
import statistics

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged

from odoo.addons.cmc_base.models import cmc_utils


@tagged("post_install", "-at_install")
class TestWp11Sampling(TransactionCase):
    # ---- inverse CDF ---------------------------------------------------------------

    def test_inverse_cdf_uniform(self):
        # RSK-01: uniform(2, 6) quantile 25% = 3
        self.assertAlmostEqual(cmc_utils.inverse_cdf("uniform", {"low": 2.0, "high": 6.0}, 0.25), 3.0)

    def test_inverse_cdf_triangular_both_sides_of_mode(self):
        # RSK-01: triangular(0, 2, 10): F(mode) = 0.2; p 0.1 -> sqrt(2), p 0.9 -> 10 - sqrt(8)
        params = {"low": 0.0, "mode": 2.0, "high": 10.0}
        self.assertAlmostEqual(cmc_utils.inverse_cdf("triangular", params, 0.1), 1.414214, places=6)
        self.assertAlmostEqual(cmc_utils.inverse_cdf("triangular", params, 0.9), 7.171573, places=6)
        self.assertAlmostEqual(cmc_utils.inverse_cdf("triangular", {"low": 0.0, "mode": 5.0, "high": 10.0}, 0.5), 5.0)

    def test_inverse_cdf_normal(self):
        # RSK-01: N(0, 1) at 97.5% = 1.959964; the median of N(100, 15) is 100
        self.assertAlmostEqual(cmc_utils.inverse_cdf("normal", {"mean": 0.0, "stdev": 1.0}, 0.975), 1.959964, places=6)
        self.assertAlmostEqual(cmc_utils.inverse_cdf("normal", {"mean": 100.0, "stdev": 15.0}, 0.5), 100.0)

    def test_invalid_params_raise(self):
        # RSK-01: a distribution the user mistyped never samples silently
        for distribution, params in (
            ("normal", {"mean": 0.0, "stdev": -1.0}),
            ("triangular", {"low": 5.0, "mode": 1.0, "high": 10.0}),
            ("triangular", {"low": 5.0, "mode": 5.0, "high": 5.0}),
            ("uniform", {"low": 3.0, "high": 3.0}),
            ("lognormal", {"mean": 0.0, "stdev": 1.0}),
        ):
            with self.subTest(distribution=distribution, params=params), self.assertRaises(ValueError):
                cmc_utils.draw(distribution, params, 10, random.Random(1))

    # ---- draw ----------------------------------------------------------------------

    def test_draw_is_deterministic_for_a_seed(self):
        # AD-114: fixed seed per run -> the same samples, whatever the global random state
        params = {"mean": 10.0, "stdev": 2.0}
        first = cmc_utils.draw("normal", params, 50, random.Random(42), lhs=True)
        random.seed(999)
        random.random()
        second = cmc_utils.draw("normal", params, 50, random.Random(42), lhs=True)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 50)
        self.assertNotEqual(first, cmc_utils.draw("normal", params, 50, random.Random(43), lhs=True))

    def test_draw_rejects_empty_sample(self):
        # RSK-01: N trials >= 1
        with self.assertRaises(ValueError):
            cmc_utils.draw("uniform", {"low": 0.0, "high": 1.0}, 0, random.Random(1))

    def test_lhs_one_sample_per_stratum(self):
        # RSK-01 / LCC App A: LHS = n equal-probability intervals, exactly one sample in each
        samples = cmc_utils.draw("uniform", {"low": 0.0, "high": 1.0}, 10, random.Random(7), lhs=True)
        self.assertEqual(sorted(int(s * 10) for s in samples), list(range(10)))
        # strata are shuffled: the draw order is not the stratum order
        self.assertNotEqual(samples, sorted(samples))

    def test_lhs_normal_moments_are_tight(self):
        # RSK-01: LHS reproduces the moments far better than 1/sqrt(N)
        samples = cmc_utils.draw("normal", {"mean": 0.0, "stdev": 1.0}, 1000, random.Random(3), lhs=True)
        self.assertAlmostEqual(statistics.fmean(samples), 0.0, delta=0.01)
        self.assertAlmostEqual(statistics.stdev(samples), 1.0, delta=0.02)

    def test_srs_normal_mean_within_sampling_error(self):
        # RSK-01: plain Monte Carlo, error ~ 1/sqrt(N) = 0.01 at N = 10,000 (5 sigma tolerance)
        samples = cmc_utils.draw("normal", {"mean": 5.0, "stdev": 1.0}, 10000, random.Random(11))
        self.assertAlmostEqual(statistics.fmean(samples), 5.0, delta=0.05)

    def test_bounded_distributions_stay_in_range(self):
        # RSK-01: triangular/uniform never leave [low, high]; triangular mean = (l + m + h) / 3
        tri = cmc_utils.draw("triangular", {"low": 8.0, "mode": 10.0, "high": 15.0}, 2000, random.Random(5), lhs=True)
        uni = cmc_utils.draw("uniform", {"low": -1.0, "high": 1.0}, 2000, random.Random(5))
        self.assertTrue(all(8.0 <= s <= 15.0 for s in tri))
        self.assertTrue(all(-1.0 <= s <= 1.0 for s in uni))
        self.assertAlmostEqual(statistics.fmean(tri), 11.0, delta=0.02)

    # ---- percentiles / describe ------------------------------------------------------

    def test_percentile_inclusive_interpolation(self):
        # RSK-01: P50 of 1..4 = 2.5; P5 of 1..101 = 6; P0/P100 = min/max; order of the input irrelevant
        self.assertEqual(cmc_utils.percentile([4.0, 1.0, 3.0, 2.0], 50), 2.5)
        self.assertAlmostEqual(cmc_utils.percentile(list(range(101, 0, -1)), 5), 6.0)
        self.assertEqual(cmc_utils.percentile([3.0, -1.0, 7.0], 0), -1.0)
        self.assertEqual(cmc_utils.percentile([3.0, -1.0, 7.0], 100), 7.0)
        self.assertEqual(cmc_utils.percentile([42.0], 95), 42.0)

    def test_percentile_invalid(self):
        for values, q in (([], 50), ([1.0], -1), ([1.0], 101)):
            with self.subTest(values=values, q=q), self.assertRaises(ValueError):
                cmc_utils.percentile(values, q)

    def test_describe_statistics(self):
        # RSK-01: forecast-cell statistics; P(profit > 0) counts strictly positive outcomes
        stats = cmc_utils.describe([-2.0, -1.0, 0.0, 1.0, 2.0, 3.0, 4.0])
        self.assertAlmostEqual(stats["mean"], 1.0)
        self.assertAlmostEqual(stats["stdev"], 2.160247, places=6)
        self.assertEqual((stats["min"], stats["max"]), (-2.0, 4.0))
        self.assertAlmostEqual(stats["p5"], -1.7)
        self.assertAlmostEqual(stats["p50"], 1.0)
        self.assertAlmostEqual(stats["p95"], 3.7)
        self.assertAlmostEqual(stats["prob_positive"], 4 / 7)
        self.assertEqual(cmc_utils.describe([5.0])["stdev"], 0.0)

    # ---- histogram ---------------------------------------------------------------------

    def test_histogram_equal_width_last_bin_closed(self):
        # RSK-01 (bins only, AD-114: samples are not stored): 0..10 in 5 bins of width 2, 10 falls in the last
        self.assertEqual(
            cmc_utils.histogram([float(v) for v in range(11)], 5),
            [(0.0, 2.0, 2), (2.0, 4.0, 2), (4.0, 6.0, 2), (6.0, 8.0, 2), (8.0, 10.0, 3)],
        )

    def test_histogram_constant_and_invalid(self):
        self.assertEqual(cmc_utils.histogram([3.0, 3.0, 3.0], 10), [(3.0, 3.0, 3)])
        with self.assertRaises(ValueError):
            cmc_utils.histogram([1.0, 2.0], 0)
        with self.assertRaises(ValueError):
            cmc_utils.histogram([], 5)

    # ---- rank correlation ---------------------------------------------------------------

    def test_ranks_average_ties(self):
        # RSK-02: Spearman uses average ranks for ties
        self.assertEqual(cmc_utils.ranks([10.0, 20.0, 20.0, 30.0]), [1.0, 2.5, 2.5, 4.0])
        self.assertEqual(cmc_utils.ranks([3.0, 1.0, 2.0]), [3.0, 1.0, 2.0])

    def test_spearman_hand_case_with_ties(self):
        # RSK-02: ranks y = 1, 2, 3.5, 5, 3.5 -> rho = 8 / sqrt(95) = 0.820783
        self.assertAlmostEqual(cmc_utils.spearman([1, 2, 3, 4, 5], [5, 6, 7, 8, 7]), 0.820783, places=6)

    def test_spearman_monotone_and_degenerate(self):
        # RSK-02: rank correlation sees any monotone relation as +/-1; a constant series carries no information
        xs = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
        self.assertAlmostEqual(cmc_utils.spearman(xs, [x**3 for x in xs]), 1.0)
        self.assertAlmostEqual(cmc_utils.spearman(xs, [-x for x in xs]), -1.0)
        self.assertEqual(cmc_utils.spearman(xs, [2.0] * 6), 0.0)
        with self.assertRaises(ValueError):
            cmc_utils.spearman([1.0, 2.0], [1.0])
        with self.assertRaises(ValueError):
            cmc_utils.spearman([1.0], [1.0])


@tagged("post_install", "-at_install")
class TestWp11EvaluatorRegistry(TransactionCase):
    def test_registry_hook_exists_with_unique_keys(self):
        # AD-102: producers register evaluators in cmc_base; cmc_scenario never depends on them
        evaluators = self.env["cmc.plan.version"]._cmc_scenario_evaluators()
        self.assertIsInstance(evaluators, list)
        keys = [entry["key"] for entry in evaluators]
        self.assertEqual(len(keys), len(set(keys)))
        for entry in evaluators:
            self.assertTrue(callable(entry["prepare"]))

    def test_unknown_evaluator_raises(self):
        # RSK-01: a simulation pointing at an evaluator no module provides fails loud
        with self.assertRaisesRegex(UserError, "wp11_no_such_evaluator"):
            self.env["cmc.plan.version"]._cmc_scenario_evaluator("wp11_no_such_evaluator")
