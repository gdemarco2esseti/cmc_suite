"""WP-10 / AD-119 - discounting and indifference maths as pure stdlib functions in ``cmc_utils``.

Sources: gap register DIF-03, DIF-06, DC-08; Cinquini II.5 (A: v=8, CF=200 vs B: v=6, CF=500), III.4.2
(Q* = CFr / (Pe - v)); LCC Ch.5-6 (NPV 20y 6%, flows at the start of each year -> 121,581; r = 0
allowed; real vs nominal); PCM Ch.2/Ch.4 (NPV, IRR, payback).

CONTRACT (``odoo.addons.cmc_base.models.cmc_utils``, Python stdlib only, no Odoo env)
- Rates are FRACTIONS (0.10 = 10%). ``flows[t]`` is the net cash flow of period t, ``flows[0]`` at t = 0
  (not discounted).
- ``npv(rate, flows)`` -> float = Σ flows[t] / (1 + rate) ** t; ``rate`` = 0 -> plain sum.
- ``irr(flows)`` -> float rate with npv = 0, found by bisection (stdlib only), precision <= 1e-7;
  raises ValueError when the flows do not change sign (no IRR). RN-002: raises ``cmc_utils.MultipleIrrError``
  (subclass of ValueError, so every ``except ValueError`` still holds) when the flows change sign more than
  once (zeros ignored): a non-conventional pattern may have several IRRs and none is meaningful.
- ``payback(flows)`` -> fractional periods at which the cumulative flow reaches 0 (linear inside the
  period), None when it never does; ``discounted_payback(rate, flows)`` same on the present values.
  RN-002: both return None when ``flows[0] >= 0`` (no investment at t = 0: nothing to pay back).
- ``nominal_rate(real, inflation)`` = (1 + real) * (1 + inflation) - 1 and ``real_rate(nominal, inflation)``
  = (1 + nominal) / (1 + inflation) - 1 (Fisher, exact - never the "nominal - inflation" approximation).
- ``indifference_qty(fixed_a, var_a, fixed_b, var_b)`` -> Q where fixed_a + var_a*Q = fixed_b + var_b*Q,
  None when var_a == var_b (parallel structures).
"""

from odoo.tests.common import TransactionCase, tagged

from odoo.addons.cmc_base.models import cmc_utils


@tagged("post_install", "-at_install")
class TestWp10FinancialMaths(TransactionCase):
    # ---- NPV -----------------------------------------------------------------------

    def test_npv_annuity_from_year_zero(self):
        # DIF-06 / LCC Ch.5: 20 yearly flows of 10,000 at 6%, first at t=0 -> 121,581.16
        self.assertAlmostEqual(cmc_utils.npv(0.06, [10000.0] * 20), 121581.16, places=2)

    def test_npv_with_initial_investment(self):
        # DIF-06: -1000 + 600/1.1 + 600/1.21 = 41.32
        self.assertAlmostEqual(cmc_utils.npv(0.10, [-1000.0, 600.0, 600.0]), 41.3223, places=4)

    def test_npv_at_zero_rate_is_the_sum(self):
        # DIF-06 / LCC: r = 0 is a legitimate choice (public sector, sustainability)
        self.assertEqual(cmc_utils.npv(0.0, [-1000.0, 600.0, 600.0]), 200.0)

    # ---- IRR -----------------------------------------------------------------------

    def test_irr_one_period(self):
        # DIF-06: -100 then +110 -> exactly 10%
        self.assertAlmostEqual(cmc_utils.irr([-100.0, 110.0]), 0.10, places=6)

    def test_irr_two_periods(self):
        # DIF-06: 600x^2 + 600x - 1000 = 0, x = 1/(1+r) -> r = 13.0662%
        rate = cmc_utils.irr([-1000.0, 600.0, 600.0])
        self.assertAlmostEqual(rate, 0.1306624, places=6)
        self.assertAlmostEqual(cmc_utils.npv(rate, [-1000.0, 600.0, 600.0]), 0.0, places=4)

    def test_irr_without_sign_change_raises(self):
        # DIF-06: no investment outflow -> no IRR, never a made-up number
        with self.assertRaises(ValueError):
            cmc_utils.irr([100.0, 50.0])

    def test_irr_multiple_sign_changes_raises_specific_error(self):
        # RN-002: -100, +230, -132 -> 100x^2 - 230x + 132 = 0, x = 1.1 and 1.2: two IRRs (10%, 20%)
        self.assertAlmostEqual(cmc_utils.npv(0.10, [-100.0, 230.0, -132.0]), 0.0, places=9)
        self.assertAlmostEqual(cmc_utils.npv(0.20, [-100.0, 230.0, -132.0]), 0.0, places=9)
        self.assertTrue(issubclass(cmc_utils.MultipleIrrError, ValueError))
        with self.assertRaises(cmc_utils.MultipleIrrError):
            cmc_utils.irr([-100.0, 230.0, -132.0])

    def test_irr_zeros_do_not_count_as_sign_changes(self):
        # RN-002: a deferred conventional investment (0, -1000, 600, 600) keeps its single IRR
        self.assertAlmostEqual(cmc_utils.irr([0.0, -1000.0, 600.0, 600.0]), 0.1306624, places=6)

    # ---- payback -------------------------------------------------------------------

    def test_payback_without_initial_investment(self):
        # RN-002: no outflow at t = 0 -> None, never "0 periods"
        self.assertIsNone(cmc_utils.payback([0.0, -1000.0, 600.0, 600.0]))
        self.assertIsNone(cmc_utils.payback([100.0, 50.0]))
        self.assertIsNone(cmc_utils.discounted_payback(0.10, [0.0, -1000.0, 600.0, 600.0]))

    def test_payback_fractional(self):
        # DIF-06: cumulative -1000, -700, -300, +200 -> 2 + 300/500 = 2.6
        self.assertAlmostEqual(cmc_utils.payback([-1000.0, 300.0, 400.0, 500.0]), 2.6, places=6)

    def test_payback_never_reached(self):
        # DIF-06: the investment is never recovered -> None, not the horizon
        self.assertIsNone(cmc_utils.payback([-1000.0, 300.0, 300.0]))

    def test_discounted_payback(self):
        # DIF-06: PV 545.45 and 495.87 -> 1 + 454.55/495.87 = 1.9167
        self.assertAlmostEqual(cmc_utils.discounted_payback(0.10, [-1000.0, 600.0, 600.0]), 1.0 + 11.0 / 12.0, places=6)

    # ---- inflation -----------------------------------------------------------------

    def test_fisher_rates(self):
        # DIF-06 / LCC Ch.5: (1+10%) x (1+5%) - 1 = 15.5% and back, exactly
        self.assertAlmostEqual(cmc_utils.nominal_rate(0.10, 0.05), 0.155, places=9)
        self.assertAlmostEqual(cmc_utils.real_rate(0.155, 0.05), 0.10, places=9)

    def test_real_and_nominal_views_agree(self):
        # DIF-06: real flows at the real rate = inflated flows at the nominal rate
        real = cmc_utils.npv(0.10, [-1000.0, 600.0, 600.0])
        nominal = cmc_utils.npv(cmc_utils.nominal_rate(0.10, 0.05), [-1000.0, 630.0, 661.5])
        self.assertAlmostEqual(real, nominal, places=6)

    # ---- indifference point --------------------------------------------------------

    def test_indifference_between_structures(self):
        # DC-08 / Cinquini II.5: 200 + 8Q = 500 + 6Q -> Q = 150
        self.assertAlmostEqual(cmc_utils.indifference_qty(200.0, 8.0, 500.0, 6.0), 150.0, places=6)

    def test_indifference_make_or_buy(self):
        # DIF-03 / Cinquini III.4.2: make 20,000 + 8Q vs buy 12Q -> Q* = 5,000
        self.assertAlmostEqual(cmc_utils.indifference_qty(20000.0, 8.0, 0.0, 12.0), 5000.0, places=6)

    def test_parallel_structures_have_no_indifference(self):
        # DC-08: same variable cost -> one structure always cheaper, no Q*
        self.assertIsNone(cmc_utils.indifference_qty(200.0, 8.0, 500.0, 8.0))
