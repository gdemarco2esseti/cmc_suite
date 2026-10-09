"""FC-08 / STD-04 - over/under-absorbed overhead per center and period; overhead spending / efficiency / volume variances.

CONTRACT: common.py §5 (absorption lines). Fixture: Z fixed rate 10, variable 6, practical 120 h/year (D = 10 h in a
month), actual indirect 105 fixed + 40 variable, AH 6 h (MO of F), SH 5 h.
"""

from odoo.tests.common import tagged

from .common import StdCase


@tagged("post_install", "-at_install")
class TestStdAbsorption(StdCase):
    def _absorption(self, run, center):
        return run.absorption_ids.filtered(lambda line: line.center_id == center)

    def test_over_under_absorbed_on_actual_base(self):
        # FC-08: applied = predetermined rate x actual base; over/under = actual - applied (here under-absorbed)
        line = self._absorption(self._run(self._std_version()), self.z)
        self.assertRecordValues(line, [{
            "driver_id": self.hours.id, "fixed_rate": 10.0, "variable_rate": 6.0, "actual_qty": 6.0, "standard_qty": 5.0,
            "capacity_qty": 10.0, "budget_fixed": -100.0, "actual_fixed": -105.0, "actual_variable": -40.0, "actual_amount": -145.0,
            "applied_actual": -96.0, "applied_standard": -80.0, "over_under_absorbed": -49.0,
        }])

    def test_overhead_spending_efficiency_volume(self):
        # STD-04: variable spending / efficiency, fixed spending / volume; they explain actual - applied at standard
        line = self._absorption(self._run(self._std_version()), self.z)
        self.assertRecordValues(line, [{
            "spending_variable": -4.0, "efficiency_variable": -6.0, "spending_fixed": -5.0, "volume_variance": -50.0,
        }])
        self.assertAlmostEqual(
            line.spending_variable + line.efficiency_variable + line.spending_fixed + line.volume_variance,
            line.actual_amount - line.applied_standard,
        )

    def test_normal_capacity_changes_volume_variance(self):
        # FC-08: with the normal capacity (96 h, D = 8 h) the fixed rate is 12.5 and the volume variance shrinks
        line = self._absorption(self._run(self._std_version(basis="normal")), self.z)
        self.assertAlmostEqual(line.fixed_rate, 12.5)
        self.assertAlmostEqual(line.capacity_qty, 8.0)
        self.assertAlmostEqual(line.volume_variance, (5.0 - 8.0) * 12.5)
        self.assertAlmostEqual(line.applied_actual, -(12.5 + 6.0) * 6.0)

    def test_only_centers_with_overhead_rate(self):
        # FC-08: Y has a labour rate but no predetermined overhead rate: no absorption row (its 77 stays in the center)
        run = self._run(self._std_version())
        self.assertEqual(run.absorption_ids.center_id, self.z)

    def test_direct_lines_and_other_periods_not_overhead(self):
        # FC-08: the MP/MOD direct lines on Z and the 999 of last year are not actual overhead of the period
        line = self._absorption(self._run(self._std_version()), self.z)
        self.assertAlmostEqual(line.actual_amount, -145.0)

    def test_absorption_snapshot(self):
        # AD-113: an overhead posted after Compute changes nothing until reset + compute
        run = self._run(self._std_version())
        self._indirect(self.z, 10, "fixed")
        self.assertAlmostEqual(self._absorption(run, self.z).actual_fixed, -105.0)
        run.action_reset_to_draft()
        self.assertFalse(run.absorption_ids)
        run.action_compute()
        self.assertAlmostEqual(self._absorption(run, self.z).actual_fixed, -115.0)
