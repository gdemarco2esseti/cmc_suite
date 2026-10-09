"""WP-15 / AD-119 - pure project-control maths in ``cmc_base/models/cmc_utils.py`` (stdlib only).

CONTRACT (binding for the developer; tests are read-only)
- ``pert_estimate(o, m, p) -> (expected, sigma)``: E = (O + 4M + P) / 6, sigma = (P - O) / 6 (PRJ-01).
  ValueError unless ``0 <= o <= m <= p``.
- ``triangular_estimate(o, m, p) -> float``: (O + M + P) / 3; same ValueError.
- ``pert_total(estimates, z=2.0) -> (expected, sigma, low, high)``: ``estimates`` = iterable of (o, m, p);
  expected = sum of E, sigma = sqrt(sum sigma^2), low/high = expected -/+ z x sigma (z = 2: ~95% band).
  Empty iterable -> (0.0, 0.0, 0.0, 0.0).
- ``emv(probability, impact) -> float``: probability as a FRACTION 0..1 (ValueError outside), impact signed
  (an opportunity is negative). ``contingency_reserve(risks)`` = sum of ``emv`` over (probability, impact)
  pairs (PRJ-02, CR = sum P x I).
- ``evm_indices(bac, pv, ev, ac) -> dict`` (PRJ-04) with keys ``cv`` = EV - AC, ``sv`` = EV - PV,
  ``cpi`` = EV / AC, ``spi`` = EV / PV, ``eac`` = BAC / CPI, ``eac_atypical`` = AC + (BAC - EV),
  ``eac_composite`` = AC + (BAC - EV) / (CPI x SPI), ``etc`` = EAC - AC, ``vac`` = BAC - EAC,
  ``tcpi`` = (BAC - EV) / (BAC - AC). An undefined ratio (zero denominator) is ``None``, and so is every
  figure built on it (AC = 0 -> cpi, eac, etc, vac, eac_composite None; eac_atypical stays defined).
- ``control_breaches(values, lower, upper) -> list[int]``: indexes of the values strictly outside
  [lower, upper] (PRJ-03 control limits).
- ``rule_of_seven(values, center=0.0, run=7) -> bool``: True when ``run`` or more CONSECUTIVE values sit
  strictly on the same side of ``center``; a value equal to the center breaks the run (PRJ-03).
Module-level access (``cmc_utils.<name>``) so a missing helper fails its own test, not the whole suite.
"""

import math

from odoo.tests.common import TransactionCase, tagged

from odoo.addons.cmc_base.models import cmc_utils


@tagged("post_install", "-at_install")
class TestWp15Pert(TransactionCase):
    def test_pert_estimate_hand_numbers(self):
        # PRJ-01: O 80, M 100, P 150 -> E = 630/6 = 105, sigma = 70/6
        expected, sigma = cmc_utils.pert_estimate(80, 100, 150)
        self.assertAlmostEqual(expected, 105.0)
        self.assertAlmostEqual(sigma, 70 / 6)

    def test_pert_estimate_rejects_unordered_points(self):
        # PRJ-01: optimistic <= most likely <= pessimistic, never negative
        for o, m, p in ((100, 80, 150), (80, 160, 150), (-1, 2, 3)):
            with self.assertRaises(ValueError):
                cmc_utils.pert_estimate(o, m, p)

    def test_triangular_estimate(self):
        # PRJ-01: triangular mean (O + M + P) / 3
        self.assertAlmostEqual(cmc_utils.triangular_estimate(80, 100, 150), 110.0)

    def test_pert_total_band_is_root_sum_of_squares(self):
        # PRJ-01: range = sqrt(sum sigma^2), 95% band = E +/- 2 sigma; hand: sigmas 1750/3, 5600/3, 1750/3
        expected, sigma, low, high = cmc_utils.pert_total([(4000, 5000, 7500), (19100, 22300, 30300), (2600, 3600, 6100)])
        self.assertAlmostEqual(expected, 5250 + 23100 + 3850, places=6)
        self.assertAlmostEqual(sigma, math.sqrt(4165000.0), places=6)
        self.assertAlmostEqual(low, 32200 - 2 * math.sqrt(4165000.0), places=6)
        self.assertAlmostEqual(high, 32200 + 2 * math.sqrt(4165000.0), places=6)

    def test_pert_total_custom_z_and_empty(self):
        # PRJ-01: z is a parameter (1 sigma ~ 68%); no estimate -> zero band
        expected, sigma, low, high = cmc_utils.pert_total([(80, 100, 150)], z=1.0)
        self.assertAlmostEqual(low, 105 - 70 / 6)
        self.assertAlmostEqual(high, 105 + 70 / 6)
        self.assertEqual(cmc_utils.pert_total([]), (0.0, 0.0, 0.0, 0.0))

    def test_emv_and_contingency_reserve(self):
        # PRJ-02: CR = sum P x I, opportunities (negative impact) net the threats: 2000 + 1000 - 600 = 2400
        self.assertAlmostEqual(cmc_utils.emv(0.4, 5000), 2000.0)
        self.assertAlmostEqual(cmc_utils.contingency_reserve([(0.4, 5000), (0.25, 4000), (0.5, -1200)]), 2400.0)
        self.assertEqual(cmc_utils.contingency_reserve([]), 0.0)

    def test_emv_rejects_probability_outside_unit_interval(self):
        # PRJ-02: a probability is a fraction 0..1
        for probability in (-0.1, 1.2):
            with self.assertRaises(ValueError):
                cmc_utils.emv(probability, 1000)

    def test_evm_indices_hand_numbers(self):
        # PRJ-04 (PCM Ch10): BAC 32200, PV 20650, EV 16800, AC 17000
        res = cmc_utils.evm_indices(32200, 20650, 16800, 17000)
        cpi, spi = 16800 / 17000, 16800 / 20650
        expected = {
            "cv": -200.0, "sv": -3850.0, "cpi": cpi, "spi": spi, "eac": 32200 / cpi,
            "eac_atypical": 32400.0, "eac_composite": 17000 + 15400 / (cpi * spi),
            "etc": 32200 / cpi - 17000, "vac": 32200 - 32200 / cpi, "tcpi": 15400 / 15200,
        }
        self.assertEqual(set(res), set(expected))
        for key, value in expected.items():
            self.assertAlmostEqual(res[key], value, places=6, msg=key)

    def test_evm_indices_undefined_ratios_are_none(self):
        # PRJ-04: no actual cost yet -> no CPI and nothing built on it; BAC = AC -> no TCPI
        res = cmc_utils.evm_indices(1000, 200, 0, 0)
        for key in ("cpi", "eac", "etc", "vac", "eac_composite"):
            self.assertIsNone(res[key], key)
        self.assertAlmostEqual(res["eac_atypical"], 1000.0)
        self.assertAlmostEqual(res["spi"], 0.0)
        self.assertIsNone(cmc_utils.evm_indices(1000, 0, 500, 1000)["tcpi"])
        self.assertIsNone(cmc_utils.evm_indices(1000, 0, 500, 1000)["spi"])

    def test_control_breaches(self):
        # PRJ-03: a point strictly outside the limits is out of control; on the limit is in control
        self.assertEqual(cmc_utils.control_breaches([-375, -755, 930, 800, -801], -800, 800), [2, 4])

    def test_rule_of_seven(self):
        # PRJ-03: 7 consecutive points on one side of the center line = out of control even inside the limits
        self.assertTrue(cmc_utils.rule_of_seven([5, -1, 1, 2, 3, 4, 5, 6, 7]))
        self.assertFalse(cmc_utils.rule_of_seven([1, 2, 3, 4, 5, 6, -1, 1]))
        # a point on the center breaks the run
        self.assertFalse(cmc_utils.rule_of_seven([1, 2, 3, 0, 4, 5, 6, 7]))
        self.assertTrue(cmc_utils.rule_of_seven([-1, -2, -3, -4, -5, -6, -7]))
        self.assertTrue(cmc_utils.rule_of_seven([11, 12, 13], center=10.0, run=3))
