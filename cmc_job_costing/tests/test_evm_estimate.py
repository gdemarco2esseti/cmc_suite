"""WP-15 / PRJ-01 - bottom-up estimate on the WBS (tasks), rate card from the registry (AD-108), PERT band.

CONTRACT: see ``evm_common`` (estimate lines, ``action_cmc_evm_plan``, version estimate totals).
"""

import math
from datetime import date

from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import tagged

from .evm_common import APR, FEB, JAN, EvmCase


@tagged("post_install", "-at_install")
class TestEvmEstimate(EvmCase):
    def test_prj01_task_estimate_pert_from_rate_card(self):
        # PRJ-01: hours O/M/P x rate of the version rate card + material -> three-point cost and PERT E / sigma per task
        version = self._baseline(approve=False)
        version.action_cmc_evm_plan()
        lines = version.estimate_line_ids.sorted(lambda line: line.task_id.name)
        # sorted by task name: Build (T2), Design (T1), Test (T3)
        expected = [
            (self.t2, 80.0, 250.0, 19100.0, 22300.0, 30300.0, 23100.0, 11200 / 6),
            (self.t1, 50.0, 105.0, 4000.0, 5000.0, 7500.0, 5250.0, 3500 / 6),
            (self.t3, 50.0, 65.0, 2600.0, 3600.0, 6100.0, 3850.0, 3500 / 6),
        ]
        self.assertEqual(lines.task_id, self.t2 | self.t1 | self.t3)
        for line, (task, rate, hours, cost_o, cost_m, cost_p, cost_e, sigma) in zip(lines, expected, strict=True):
            self.assertEqual(line.task_id, task)
            self.assertAlmostEqual(line.hourly_rate, rate, places=4)
            self.assertAlmostEqual(line.hours_expected, hours, places=4)
            self.assertAlmostEqual(line.cost_optimistic, cost_o, places=2)
            self.assertAlmostEqual(line.cost_most_likely, cost_m, places=2)
            self.assertAlmostEqual(line.cost_pessimistic, cost_p, places=2)
            self.assertAlmostEqual(line.cost_expected, cost_e, places=2)
            self.assertAlmostEqual(line.cost_sigma, sigma, places=2)

    def test_prj01_project_estimate_accuracy_band(self):
        # PRJ-01: project E = sum E, sigma = sqrt(sum sigma^2), 95 % band E +/- 2 sigma
        version = self._baseline(approve=False)
        version.action_cmc_evm_plan()
        sigma = math.sqrt(4165000.0)
        self.assertAlmostEqual(version.estimate_expected, 32200.0, places=2)
        self.assertAlmostEqual(version.estimate_sigma, sigma, places=2)
        self.assertAlmostEqual(version.estimate_low, 32200.0 - 2 * sigma, places=2)
        self.assertAlmostEqual(version.estimate_high, 32200.0 + 2 * sigma, places=2)

    def test_prj01_rate_card_is_the_version_own(self):
        # PRJ-01 / AD-108: the estimate reads the rate card of ITS version; another version's card never counts
        other = self._baseline(approve=False, risks=False, rate_card=((self.z, JAN, 60.0), (self.y, FEB, 90.0), (self.z, APR, 60.0)))
        version = self._baseline(approve=False)
        version.action_cmc_evm_plan()
        other.action_cmc_evm_plan()
        design = version.estimate_line_ids.filtered(lambda line: line.task_id == self.t1)
        self.assertAlmostEqual(design.hourly_rate, 50.0, places=4)
        self.assertAlmostEqual(other.estimate_line_ids.filtered(lambda line: line.task_id == self.t1).hourly_rate, 60.0, places=4)

    def test_prj01_missing_rate_names_the_center(self):
        # PRJ-01: an hour estimate without a rate in the card is an error, never a silent zero
        estimates = self._estimate_vals()
        estimates[2]["center_id"] = self.y.id  # Y has a rate in February only, the Test task starts in April
        version = self._baseline(approve=False, estimates=estimates)
        with self.assertRaises(UserError) as caught:
            version.action_cmc_evm_plan()
        self.assertIn(self.y.name, str(caught.exception))

    def test_prj01_material_only_line_needs_no_rate(self):
        # PRJ-01: a material/subcontract estimate without hours is costed without the rate card
        version = self._baseline(approve=False, risks=False, estimates=[
            {"name": "Purchase", "task_id": self.t2.id, "hours_optimistic": 0, "hours_most_likely": 0, "hours_pessimistic": 0,
             "material_cost": 1200.0, "date_start": FEB, "date_end": date(2026, 2, 28)},
        ], rate_card=())
        version.action_cmc_evm_plan()
        self.assertAlmostEqual(version.estimate_expected, 1200.0, places=2)
        self.assertAlmostEqual(version.estimate_sigma, 0.0, places=2)

    def test_prj01_three_points_must_be_ordered(self):
        # PRJ-01: optimistic <= most likely <= pessimistic
        version = self._baseline(approve=False, risks=False)
        line = version.estimate_line_ids[:1]
        with self.assertRaises(ValidationError):
            line.hours_optimistic = line.hours_most_likely + 1
        with self.assertRaises(ValidationError):
            line.hours_pessimistic = line.hours_most_likely - 1

    def test_prj01_hours_need_a_resource_center(self):
        # PRJ-01: hours are valued at a center rate: no center, no estimate
        estimates = self._estimate_vals()[:1]
        estimates[0]["center_id"] = False
        with self.assertRaises(ValidationError):
            self._baseline(approve=False, risks=False, estimates=estimates)

    def test_prj01_task_must_belong_to_the_project(self):
        # PRJ-01: the WBS of a baseline is the task tree of its project
        estimates = self._estimate_vals()[:1]
        estimates[0]["task_id"] = self.tq.id
        with self.assertRaises(ValidationError):
            self._baseline(approve=False, risks=False, estimates=estimates)

    def test_prj01_schedule_inside_the_version_span(self):
        # PRJ-01 / AD-105: a work package is scheduled inside its baseline span, start before end
        for start, end in ((JAN, date(2026, 7, 31)), (date(2025, 12, 1), FEB), (APR, FEB)):
            estimates = self._estimate_vals()[:1]
            estimates[0].update(date_start=start, date_end=end)
            with self.subTest(start=start, end=end), self.assertRaises(ValidationError):
                self._baseline(approve=False, risks=False, estimates=estimates)
