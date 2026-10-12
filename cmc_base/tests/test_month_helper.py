"""AD-105 - calendar-month grain helpers (wave A0).

CONTRACT (pure functions, stdlib only, in ``cmc_base/models/cmc_utils.py``):

- ``month_start(d) -> date``: first day of the month of ``d``.
- ``month_end(d) -> date``: last day of the month of ``d`` (leap years included).
- ``month_range(date_from, date_to) -> list[date]``: first day of every calendar month
  touched by the span, both ends inclusive, in order; ``ValueError`` when
  ``date_to < date_from``.
- ``phase_by_month(total, date_from, date_to, rounding, weights=None) -> dict[date, float]``:
  split ``total`` over ``month_range(date_from, date_to)`` through the existing ``spread()``
  (conserving: the parts re-sum exactly to ``total``). ``weights`` is an optional
  seasonality profile ``{calendar month number 1..12: weight}``; a month missing from it
  weighs 0; ``None`` = equal weight per month (per MONTH, not per day). ``ValueError``
  when the weights of the months in the span sum to 0.
"""

from datetime import date

from odoo.tests.common import TransactionCase, tagged

from odoo.addons.cmc_base.models import cmc_utils


@tagged("post_install", "-at_install")
class TestMonthHelper(TransactionCase):
    """AD-105: one month bucket = the first day of the month; no period model."""

    def test_month_start(self):
        # AD-105: every fact row is keyed by the first day of its month
        self.assertEqual(cmc_utils.month_start(date(2026, 3, 17)), date(2026, 3, 1))
        self.assertEqual(cmc_utils.month_start(date(2026, 3, 1)), date(2026, 3, 1))
        self.assertEqual(cmc_utils.month_start(date(2028, 2, 29)), date(2028, 2, 1))
        self.assertEqual(cmc_utils.month_start(date(2026, 12, 31)), date(2026, 12, 1))

    def test_month_end_leap_year(self):
        # AD-105: February has 29 days in a leap year, 28 otherwise; December closes the year
        self.assertEqual(cmc_utils.month_end(date(2028, 2, 1)), date(2028, 2, 29))
        self.assertEqual(cmc_utils.month_end(date(2027, 2, 14)), date(2027, 2, 28))
        self.assertEqual(cmc_utils.month_end(date(2100, 2, 1)), date(2100, 2, 28))  # not a leap year
        self.assertEqual(cmc_utils.month_end(date(2000, 2, 1)), date(2000, 2, 29))  # leap year
        self.assertEqual(cmc_utils.month_end(date(2026, 12, 5)), date(2026, 12, 31))
        self.assertEqual(cmc_utils.month_end(date(2026, 4, 30)), date(2026, 4, 30))

    def test_month_range_touches_partial_months(self):
        # AD-105: a span from the last day of a month to the first day of another touches both
        self.assertEqual(
            cmc_utils.month_range(date(2026, 1, 31), date(2026, 3, 1)),
            [date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)],
        )

    def test_month_range_crosses_year_end(self):
        # AD-105: multi-year plans (BUD-03) use the same month grain
        self.assertEqual(
            cmc_utils.month_range(date(2026, 11, 15), date(2027, 2, 1)),
            [date(2026, 11, 1), date(2026, 12, 1), date(2027, 1, 1), date(2027, 2, 1)],
        )

    def test_month_range_single_day_and_leap_day(self):
        # AD-105: a one-day span is one month; 29 Feb is a valid bound
        self.assertEqual(cmc_utils.month_range(date(2028, 2, 29), date(2028, 2, 29)), [date(2028, 2, 1)])
        self.assertEqual(len(cmc_utils.month_range(date(2026, 1, 1), date(2026, 12, 31))), 12)

    def test_month_range_rejects_reversed_span(self):
        # AD-105: an inverted span is a programming error, never an empty list
        with self.assertRaises(ValueError):
            cmc_utils.month_range(date(2026, 3, 1), date(2026, 2, 28))

    def test_phase_equal_is_conserving(self):
        # AD-105 + spread(): 1000 over 3 months loses no cent
        parts = cmc_utils.phase_by_month(1000.0, date(2026, 1, 1), date(2026, 3, 31), 0.01)
        self.assertEqual(sorted(parts), [date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)])
        self.assertAlmostEqual(sum(parts.values()), 1000.0, places=2)
        for value in parts.values():
            self.assertIn(round(value, 2), (333.33, 333.34))

    def test_phase_equal_is_per_month_not_per_day(self):
        # AD-105: February of a leap year weighs as much as January (month grain, not day grain)
        parts = cmc_utils.phase_by_month(200.0, date(2028, 1, 1), date(2028, 2, 29), 0.01)
        self.assertEqual(parts, {date(2028, 1, 1): 100.0, date(2028, 2, 1): 100.0})

    def test_phase_with_seasonality_weights(self):
        # BUD-02: a 12-weight seasonality profile drives the phasing
        parts = cmc_utils.phase_by_month(400.0, date(2026, 1, 1), date(2026, 3, 31), 0.01, weights={1: 1, 2: 1, 3: 2})
        self.assertEqual(parts, {date(2026, 1, 1): 100.0, date(2026, 2, 1): 100.0, date(2026, 3, 1): 200.0})

    def test_phase_weights_across_year_end(self):
        # BUD-03: weights are keyed by calendar month, so a span over the year end uses Dec and Jan
        parts = cmc_utils.phase_by_month(100.0, date(2026, 12, 1), date(2027, 1, 31), 0.01, weights={12: 3, 1: 1})
        self.assertEqual(parts, {date(2026, 12, 1): 75.0, date(2027, 1, 1): 25.0})

    def test_phase_missing_month_weighs_zero(self):
        # BUD-02: a month absent from the profile receives nothing, the total still conserves
        parts = cmc_utils.phase_by_month(90.0, date(2026, 1, 1), date(2026, 3, 31), 0.01, weights={1: 1, 3: 2})
        self.assertEqual(parts, {date(2026, 1, 1): 30.0, date(2026, 2, 1): 0.0, date(2026, 3, 1): 60.0})

    def test_phase_zero_weight_base_rejected(self):
        # AD-105: a profile with no weight on the span cannot phase anything
        with self.assertRaises(ValueError):
            cmc_utils.phase_by_month(100.0, date(2026, 1, 1), date(2026, 2, 28), 0.01, weights={7: 1})
