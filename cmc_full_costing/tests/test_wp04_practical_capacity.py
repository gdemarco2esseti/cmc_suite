"""WP-04 / CAP-03 - charge products only for the capacity used; idle cost isolated, never re-absorbed.

CONTRACT
- ``cmc.costing.run.capacity_basis`` ``actual`` (default: the whole center pool spread over the actual
  driver, unabsorbed 0) / ``practical`` (multiple base): per final center the FIXED pool is charged at
  ``fixed_rate = fixed cost / capacity_practical`` x driver qty used by each product; the VARIABLE pool is
  spread over the actual driver as before. ``capacity_practical`` is read from the
  ``cmc.capacity.analysis`` of the center covering the run period (run company) and copied at Compute.
  A final center with cost and no capacity analysis -> ValidationError (fail loud).
- Used > practical: the whole fixed pool is charged (rate on the used quantity), unabsorbed 0, never
  negative; conservation holds.
- Outputs: ``cmc.costing.run.unabsorbed_cost`` (Σ idle cost, kept OUT of every product) and
  ``capacity_ids`` (``cmc.costing.capacity``, one row per final center: ``center_id``, ``fixed_cost``,
  ``variable_cost``, ``capacity_practical``, ``capacity_used``, ``fixed_rate``, ``fixed_charged``,
  ``unabsorbed_cost``). Σ indirect + unabsorbed = Σ final-center pools. Reset clears both.
Numbers: Z fixed 1000 / 125 h = 8/h x 100 h = 800 (P1 480, P2 320), idle 200; Z variable 200 / 100 h
(P1 120, P2 80); W 600 / 60 h = 10/h (P1 300, P2 300), idle 0. P1 indirect 900, industrial 1320, unit
13.20; P2 700, 850, 17.00.
"""

from odoo.exceptions import ValidationError
from odoo.tests.common import tagged

from .wp04_common import Wp04Case


@tagged("post_install", "-at_install")
class TestWp04PracticalCapacity(Wp04Case):
    def test_cap03_charge_used_capacity_at_practical_rate(self):
        # CAP-03: product charge = used x rate on practical capacity
        run = self._run_a(capacity_basis="practical")
        self.assertRecordValues(
            self._res(run, self.p1) | self._res(run, self.p2),
            [
                {"cost_indiretto": 900.0, "cost_industriale": 1320.0, "unit_cost_industriale": 13.20},
                {"cost_indiretto": 700.0, "cost_industriale": 850.0, "unit_cost_industriale": 17.00},
            ],
        )
        z_quota = self._res(run, self.p1).quota_ids.filtered(lambda q: q.center_id == self.z)
        self.assertRecordValues(z_quota, [{"amount": 600.0, "amount_fixed": 480.0, "amount_variable": 120.0}])

    def test_cap03_unabsorbed_cost_isolated(self):
        # CAP-03: the idle capacity cost is reported apart, per center
        run = self._run_a(capacity_basis="practical")
        self.assertAlmostEqual(run.unabsorbed_cost, 200.0, places=2)
        self.assertRecordValues(
            run.capacity_ids.filtered(lambda c: c.center_id == self.z) | run.capacity_ids.filtered(lambda c: c.center_id == self.w),
            [
                {"fixed_cost": 1000.0, "variable_cost": 200.0, "capacity_practical": 125.0, "capacity_used": 100.0,
                 "fixed_rate": 8.0, "fixed_charged": 800.0, "unabsorbed_cost": 200.0},
                {"fixed_cost": 600.0, "variable_cost": 0.0, "capacity_practical": 60.0, "capacity_used": 60.0,
                 "fixed_rate": 10.0, "fixed_charged": 600.0, "unabsorbed_cost": 0.0},
            ],
        )

    def test_cap03_conservation(self):
        # CAP-03 / INT-05: Σ product overhead + idle = final center pools; idle never re-absorbed
        run = self._run_a(capacity_basis="practical")
        indirect = sum(run.result_ids.mapped("cost_indiretto"))
        self.assertAlmostEqual(indirect, 1600.0, places=2)
        self.assertAlmostEqual(indirect + run.unabsorbed_cost, 1800.0, places=2)
        self.assertAlmostEqual(sum(run.result_ids.mapped("cost_industriale")) + run.unabsorbed_cost, 2370.0, places=2)

    def test_cap03_actual_basis_absorbs_everything(self):
        # CAP-03: the default basis keeps the v1 full absorption and reports no idle cost
        run = self._run_a()
        self.assertAlmostEqual(run.unabsorbed_cost, 0.0, places=2)
        self.assertAlmostEqual(self._res(run, self.p1).cost_indiretto, 1020.0, places=2)

    def test_cap03_missing_capacity_fails_loud(self):
        # CAP-03: no practical capacity for a center with cost -> no silent fallback to actual
        self.cap_w.unlink()
        with self.assertRaises(ValidationError):
            self._run_a(capacity_basis="practical")

    def test_cap03_used_above_practical_never_negative_idle(self):
        # Cinquini VI.2.4: used > practical -> alert case; no negative idle, no over-absorption
        self.cap_z.capacity_practical = 80
        run = self._run_a(capacity_basis="practical")
        self.assertAlmostEqual(run.unabsorbed_cost, 0.0, places=2)
        z_row = run.capacity_ids.filtered(lambda c: c.center_id == self.z)
        self.assertAlmostEqual(z_row.unabsorbed_cost, 0.0, places=2)
        self.assertAlmostEqual(z_row.fixed_charged, 1000.0, places=2)
        self.assertAlmostEqual(sum(run.result_ids.mapped("cost_indiretto")), 1800.0, places=2)

    def test_cap03_capacity_is_snapshot(self):
        # AD-113: editing the capacity analysis later never changes a computed run
        run = self._run_a(capacity_basis="practical")
        self.cap_z.capacity_practical = 150
        self.env.invalidate_all()
        self.assertAlmostEqual(run.capacity_ids.filtered(lambda c: c.center_id == self.z).capacity_practical, 125.0, places=2)
        self.assertAlmostEqual(self._res(run, self.p1).cost_indiretto, 900.0, places=2)

    def test_cap03_reset_clears_idle(self):
        run = self._run_a(capacity_basis="practical")
        run.action_reset_to_draft()
        self.assertFalse(run.capacity_ids)
        self.assertAlmostEqual(run.unabsorbed_cost, 0.0, places=2)
