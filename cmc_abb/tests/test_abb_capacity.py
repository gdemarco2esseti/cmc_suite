"""WP-12 / CAP-05 capacity time states + responsibility of unused cost; CAP-06 spending profiles and first
constraining resource (Kaplan & Cooper Ch.7, Ch.15; Cokins Exec Ch.6 CAM-I modes; Frost St.6 step-fixed).

CONTRACT (see common.py for the whole WP)
- Spending profiles on ``cmc.abc.resource``: flexible (supply = need, proportional cost), committed_fixed
  (supply = ``committed_units``; demand above it -> ``shortage_units`` = required - committed, rate =
  cost / demand), committed_step (ceil(need / unit size), 6-digit float_round so 1,600 / 800 = 2 exactly).
  ValidationError: ``unit_size`` <= 0, ``unit_cost`` < 0, ``committed_units`` < 0.
- First constraining resource per activity (fungible resources: capacity left by the other activities),
  run ``bottleneck_resource_id`` / ``mix_capacity_factor`` (committed resources, after non-productive time).
- Time states (``time_state_ids``): non-productive modes standby / maintenance / changeover / training add to
  the need; ``capacity_state_ids`` productive / nonproductive / idle with quantity, cost (x resource rate)
  and responsibility (line override, else resource ``nonproductive_responsibility_id`` /
  ``idle_responsibility_id``); Σ per resource = supply / budgeted cost; non-productive + idle = unused.
  ``_cmc_unused_by_responsibility()`` -> {responsibility id or False: cost}.
"""

from odoo.exceptions import ValidationError
from odoo.tests.common import tagged

from .common import AbbCase


@tagged("post_install", "-at_install")
class TestAbbCapacity(AbbCase):
    def _states(self):
        return [
            {"resource_id": self.tech.id, "mode": "training", "quantity": 160},
            {"resource_id": self.machr.id, "mode": "maintenance", "quantity": 200, "responsibility_id": self.maint.id},
        ]

    # ---- CAP-06 spending profiles -------------------------------------------

    def test_cap06_step_exact_boundary_is_not_rounded_up(self):
        # CAP-06: 1,600 shipments on 800-shipment trucks is 2 trucks, not 3 (float noise must not add a lump)
        run = self._abb_run(target_version_id=False, overrides=[{"activity_id": self.ship.id, "quantity": 1600}])
        self.assertRecordValues(self._res(run, self.truck), [{
            "required_units": 2.0, "supply_units": 2.0, "unused_qty": 0.0, "shortage_units": 1.0, "budgeted_cost": 50000.0,
        }])

    def test_cap06_step_supply_is_lumpy(self):
        # CAP-06 / Kaplan Ch.15: demand 1.1 trucks -> supply 2.0, unused 0.9 truck = 720 shipments
        run = self._abb_run(target_version_id=False)
        truck = self._res(run, self.truck)
        self.assertAlmostEqual(truck.required_units, 1.1, places=4)
        self.assertAlmostEqual(truck.unused_qty, 720.0, places=4)
        self.assertAlmostEqual(truck.unused_cost, 22500.0, places=2)

    def test_cap06_flexible_supply_is_proportional(self):
        # CAP-06: a flexible resource is bought as used - no unused capacity, cost follows demand
        run = self._abb_run(target_version_id=False, overrides=[{"activity_id": self.mach.id, "quantity": 700}])
        self.assertRecordValues(self._res(run, self.energy), [{
            "demand_qty": 7000.0, "supply_qty": 7000.0, "unused_qty": 0.0, "unused_cost": 0.0, "budgeted_cost": 1400.0,
        }])

    def test_cap06_committed_fixed_shortage(self):
        # CAP-06: committed-fixed supply does not grow with demand: the gap is reported as shortage
        run = self._abb_run(target_version_id=False, overrides=[{"activity_id": self.mach.id, "quantity": 2500}])
        self.assertRecordValues(self._res(run, self.machr), [{
            "supply_units": 1.0, "supply_qty": 2000.0, "unused_qty": 0.0, "required_units": 1.25,
            "shortage_units": 0.25, "budgeted_cost": 30000.0, "rate": 12.0, "assigned_cost": 30000.0,
        }])
        self.assertAlmostEqual(run.total_budgeted_cost, run.total_assigned_cost + run.total_unused_cost, places=2)

    def test_cap06_profile_validation(self):
        # CAP-06: a zero unit size makes the step function undefined
        with self.assertRaises(ValidationError):
            self.truck.unit_size = 0
            self.env.flush_all()
        with self.assertRaises(ValidationError):
            self.truck.unit_cost = -1
            self.env.flush_all()
        with self.assertRaises(ValidationError):
            self.truck.committed_units = -1
            self.env.flush_all()

    def test_cap06_first_constraining_resource_per_activity(self):
        # CAP-06 / Kaplan Ch.15: capacity_A = min_r(available_r / use_r), fungible resources net of other activities
        run = self._abb_run(target_version_id=False)
        self.assertRecordValues(self._act(run, self.setup), [{"capacity_qty": 316.0, "constraining_resource_id": self.tech.id}])
        self.assertRecordValues(self._act(run, self.mach), [{"capacity_qty": 2000.0, "constraining_resource_id": self.machr.id}])
        self.assertRecordValues(self._act(run, self.ship), [{"capacity_qty": 1600.0, "constraining_resource_id": self.truck.id}])

    def test_cap06_bottleneck_of_product_mix(self):
        # CAP-06: the planned mix can grow x1.4286 before the machines bind (TECH and TRUCK at x1.818)
        run = self._abb_run(target_version_id=False)
        self.assertEqual(run.bottleneck_resource_id, self.machr)
        self.assertAlmostEqual(run.mix_capacity_factor, 2000.0 / 1400.0, places=4)

    # ---- CAP-05 time states ----------------------------------------------------

    def test_cap05_time_states_split_supply(self):
        # CAP-05 / CAM-I: supplied = productive + non-productive + idle, each costed at the resource rate
        run = self._abb_run(target_version_id=False, time_states=self._states())
        tech = run.capacity_state_ids.filtered(lambda s: s.resource_id == self.tech)
        by_state = {(s.state, s.mode or False): s for s in tech}
        self.assertEqual(set(by_state), {("productive", False), ("nonproductive", "training"), ("idle", False)})
        self.assertRecordValues(by_state[("productive", False)], [{"quantity": 1760.0, "cost": 52800.0}])
        self.assertRecordValues(by_state[("nonproductive", "training")], [{"quantity": 160.0, "cost": 4800.0}])
        self.assertRecordValues(by_state[("idle", False)], [{"quantity": 1280.0, "cost": 38400.0}])
        self.assertAlmostEqual(sum(tech.mapped("quantity")), 3200.0, places=4)
        self.assertAlmostEqual(sum(tech.mapped("cost")), 96000.0, places=2)
        # non-productive time is part of the need: 1,920 h = 1.2 FTE, still 2 FTE supplied
        self.assertRecordValues(self._res(run, self.tech), [{"nonproductive_qty": 160.0, "required_units": 1.2, "supply_units": 2.0}])

    def test_cap05_flexible_resource_has_only_productive_time(self):
        # CAP-05: what is bought as used is never idle
        run = self._abb_run(target_version_id=False, time_states=self._states())
        energy = run.capacity_state_ids.filtered(lambda s: s.resource_id == self.energy)
        self.assertRecordValues(energy, [{"state": "productive", "quantity": 14000.0, "cost": 2800.0}])

    def test_cap05_states_reconcile_to_unused_cost(self):
        # CAP-05: Σ non-productive + idle cost = unused capacity cost; Σ all states = budget
        run = self._abb_run(target_version_id=False, time_states=self._states())
        states = run.capacity_state_ids
        self.assertAlmostEqual(sum(states.mapped("cost")), 178800.0, places=2)
        self.assertAlmostEqual(sum(states.filtered(lambda s: s.state != "productive").mapped("cost")), run.total_unused_cost, places=2)
        self.assertAlmostEqual(run.total_unused_cost, 74700.0, places=2)
        self.assertAlmostEqual(run.total_assigned_cost, 104100.0, places=2)

    def test_cap05_responsibility_of_unused_cost(self):
        # CAP-05 / Cokins Exec Ch.6: operations owns non-productive, sales owns idle; a line can override
        run = self._abb_run(target_version_id=False, time_states=self._states())
        machr = run.capacity_state_ids.filtered(lambda s: s.resource_id == self.machr)
        self.assertEqual(machr.filtered(lambda s: s.state == "nonproductive").responsibility_id, self.maint)
        self.assertEqual(machr.filtered(lambda s: s.state == "idle").responsibility_id, self.sales)
        tech_np = run.capacity_state_ids.filtered(lambda s: s.resource_id == self.tech and s.state == "nonproductive")
        self.assertEqual(tech_np.responsibility_id, self.ops)
        self.assertFalse(run.capacity_state_ids.filtered(lambda s: s.state == "productive").responsibility_id)
        unused = run._cmc_unused_by_responsibility()
        self.assertAlmostEqual(unused[self.ops.id], 4800.0, places=2)
        self.assertAlmostEqual(unused[self.maint.id], 3000.0, places=2)
        self.assertAlmostEqual(unused[self.sales.id], 66900.0, places=2)  # 38,400 + 6,000 + 22,500
        self.assertAlmostEqual(sum(unused.values()), run.total_unused_cost, places=2)

    def test_cap05_nonproductive_time_reduces_mix_capacity(self):
        # CAP-05 x CAP-06: maintenance hours are not available to products
        run = self._abb_run(target_version_id=False, time_states=self._states())
        self.assertEqual(run.bottleneck_resource_id, self.machr)
        self.assertAlmostEqual(run.mix_capacity_factor, 1800.0 / 1400.0, places=4)
        # MACH capacity on the machines: 2,000 - 200 maintenance = 1,800
        self.assertRecordValues(self._act(run, self.mach), [{"capacity_qty": 1800.0, "constraining_resource_id": self.machr.id}])

    def test_cap05_time_state_quantity_positive(self):
        # CAP-05: a zero or negative time state is a typing error
        run = self._abb_run(compute=False)
        with self.assertRaises(ValidationError):
            self.env_a["cmc.abb.time.state"].create({"run_id": run.id, "resource_id": self.tech.id, "mode": "standby", "quantity": 0})
            self.env.flush_all()
