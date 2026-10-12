"""WP-05 / CEN-04 - resource-center hourly rate table ``cmc.job.rate`` (see ``common`` CONTRACT).

CONTRACT (part)
- Monthly row (``date`` = first day of the month) per center x optional employee category.
- ``computed`` mode: ``hourly_rate = (special_cost + common_cost_share) / net_hours``, ``net_hours =
  gross_hours - absence_hours`` (e.g. 2,080 - vacation/holidays/sick); ``net_hours <= 0`` -> ValidationError.
- ``action_cmc_load_center_cost()`` fills ``special_cost`` with the month's cost of the center (dept expense).
- One row per (company, center, category, month): UniqueIndex.
Numbers: 48,000 + 16,000 over 2,080 - 480 = 1,600 h -> 40.00/h; OH March cost 750 over 160 - 10 h -> 5.00/h.
"""

from datetime import date

from psycopg2 import IntegrityError

from odoo.exceptions import ValidationError
from odoo.tests.common import tagged
from odoo.tools import mute_logger

from .common import D_MID, JobCase


@tagged("post_install", "-at_install")
class TestJobRate(JobCase):
    def test_rate_row_is_monthly(self):
        # AD-105: a rate belongs to a calendar month
        self.assertEqual(self.rate_z.date, date(2026, 3, 1))

    def test_computed_rate_from_cost_and_net_hours(self):
        # CEN-04: rate = (special costs + share of common costs) / net hours provided
        rate = self._rate(
            self.su, self.y, 0.0, category=self.senior, rate_mode="computed",
            special_cost=48000.0, common_cost_share=16000.0, gross_hours=2080.0, absence_hours=480.0,
        )
        self.assertRecordValues(rate, [{"net_hours": 1600.0, "hourly_rate": 40.0}])

    def test_computed_rate_follows_inputs(self):
        # CEN-04: the rate is a stored compute, it follows a change of the cost base
        rate = self._rate(
            self.su, self.y, 0.0, category=self.senior, rate_mode="computed",
            special_cost=48000.0, common_cost_share=16000.0, gross_hours=2080.0, absence_hours=480.0,
        )
        rate.absence_hours = 80.0
        self.assertAlmostEqual(rate.hourly_rate, 32.0, places=2)

    def test_manual_rate_kept_as_entered(self):
        # CEN-04: a manual rate card is not recomputed from empty cost fields
        self.assertEqual(self.rate_z_senior.rate_mode, "manual")
        self.assertAlmostEqual(self.rate_z_senior.hourly_rate, 55.0, places=2)

    def test_computed_rate_needs_net_hours(self):
        # CEN-04: no hours provided, no rate (a division by zero would hide the missing capacity)
        with self.assertRaises(ValidationError):
            self._rate(self.su, self.y, 0.0, category=self.senior, rate_mode="computed",
                       special_cost=1000.0, gross_hours=100.0, absence_hours=100.0)

    def test_negative_rate_refused(self):
        with self.assertRaises(ValidationError):
            self._rate(self.su, self.oh, -1.0)

    def test_load_center_cost_reads_month_expense(self):
        # CEN-04 (hourly-rate method): dept expense of the month, April distractor out
        rate = self._rate(self.su, self.oh, 0.0, rate_mode="computed", gross_hours=160.0, absence_hours=10.0)
        rate.action_cmc_load_center_cost()
        self.assertRecordValues(rate, [{"special_cost": 750.0, "net_hours": 150.0, "hourly_rate": 5.0}])

    def test_one_row_per_center_category_month(self):
        # CEN-04: two rates for the same resource and month would make the hour value ambiguous
        with mute_logger("odoo.sql_db"), self.assertRaises(IntegrityError), self.cr.savepoint():
            self._rate(self.su, self.z, 41.0, day=D_MID)

    def test_same_center_other_month_allowed(self):
        rate = self._rate(self.su, self.z, 44.0, day=date(2026, 5, 20))
        self.assertEqual(rate.date, date(2026, 5, 1))
