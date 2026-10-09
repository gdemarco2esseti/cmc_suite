"""WP-12 / FCS-03 - driver-based what-if on the ABC model (moved from WP-11, AD-118): predicted cost =
forecast driver qty x stable rates; volume / mix / process change -> resource supply and delta cost
(Kaplan & Cooper Ch.15 "100 -> 90, 2.00 -> 1.95"; Cokins Exec Ch.8; Turney Ch.6).

CONTRACT (see common.py for the whole WP)
- ``action_what_if()`` on a COMPUTED ABB run (else UserError) -> new DRAFT run: same source / measure /
  calibration, copies of bill, usage, override and time-state lines, ``baseline_run_id`` = the run,
  ``target_version_id`` empty (never budget lines, never rates).
- ``driver_override_ids`` (``activity_id``, ``quantity``) replace the computed activity demand.
- With a baseline: run ``delta_cost`` = total_budgeted_cost - baseline's; resource ``delta_supply_units``
  / ``delta_cost`` vs the baseline resource; ``predicted_cost`` = Σ demand x BASELINE activity rate.
"""

from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .common import AbbCase


@tagged("post_install", "-at_install")
class TestAbbWhatIf(AbbCase):
    def test_fcs03_what_if_copies_inputs(self):
        # FCS-03: a scenario starts from the approved model, never from scratch
        base = self._abb_run()
        whatif = base.action_what_if()
        self.assertRecordValues(whatif, [{
            "state": "draft", "baseline_run_id": base.id, "target_version_id": False,
            "source_version_id": self.fc27.id, "output_measure_id": self.units.id, "calibration_run_id": self.calib.id,
        }])
        self.assertEqual(len(whatif.bill_line_ids), 6)
        self.assertEqual(len(whatif.usage_line_ids), 6)
        self.assertNotEqual(whatif.bill_line_ids, base.bill_line_ids)

    def test_fcs03_what_if_needs_computed_baseline(self):
        # FCS-03: a delta against figures that do not exist yet means nothing
        with self.assertRaises(UserError):
            self._abb_run(compute=False).action_what_if()

    def test_fcs03_volume_change_adds_a_truck(self):
        # FCS-03: +820 shipments -> 2.125 trucks -> one more truck (+25,000); technicians absorb it in slack
        base = self._abb_run()
        whatif = base.action_what_if()
        whatif.driver_override_ids = [(0, 0, {"activity_id": self.ship.id, "quantity": 1700})]
        whatif.action_compute()
        self.assertAlmostEqual(whatif.total_budgeted_cost, 203800.0, places=2)
        self.assertAlmostEqual(whatif.delta_cost, 25000.0, places=2)
        self.assertRecordValues(self._res(whatif, self.truck), [{"supply_units": 3.0, "delta_supply_units": 1.0, "delta_cost": 25000.0}])
        self.assertRecordValues(self._res(whatif, self.tech), [{"supply_units": 2.0, "delta_supply_units": 0.0, "delta_cost": 0.0}])
        self.assertAlmostEqual(self._act(whatif, self.ship).demand_qty, 1700.0, places=4)

    def test_fcs03_predicted_cost_at_stable_rates(self):
        # FCS-03 register formula: predicted cost = forecast driver qty x stable (baseline) rates
        base = self._abb_run()
        whatif = base.action_what_if()
        whatif.driver_override_ids = [(0, 0, {"activity_id": self.ship.id, "quantity": 1700})]
        whatif.action_compute()
        self.assertAlmostEqual(whatif.predicted_cost, 28 * 150 + 1400 * 47 + 1700 * 38.75, places=2)  # 135,875
        self.assertFalse(base.predicted_cost)

    def test_fcs03_process_change_frees_a_technician(self):
        # FCS-03 / Kaplan Ch.15: halving technician time per machine hour frees one FTE (-48,000)
        base = self._abb_run()
        whatif = base.action_what_if()
        whatif.usage_line_ids.filtered(lambda u: u.activity_id == self.mach and u.resource_id == self.tech).usage_per_unit = 0.5
        whatif.action_compute()
        self.assertRecordValues(self._res(whatif, self.tech), [{
            "demand_qty": 1060.0, "supply_units": 1.0, "excess_units": 2.0, "budgeted_cost": 48000.0,
            "delta_supply_units": -1.0, "delta_cost": -48000.0,
        }])
        self.assertAlmostEqual(whatif.delta_cost, -48000.0, places=2)
        self.assertAlmostEqual(self._act(whatif, self.mach).rate, 32.0, places=4)  # 0.5 x 30 + 15 + 2

    def test_fcs03_what_if_writes_no_budget_and_no_rates(self):
        # FCS-03: a scenario must not touch the budget nor the planned rates of the target version
        base = self._abb_run()
        Rate = self.env["cmc.rate"]
        rate_domain = [("origin_model", "=", "cmc.plan.version"), ("origin_id", "=", self.bud27.id)]
        rates = Rate.search_count(rate_domain)
        whatif = base.action_what_if()
        whatif.driver_override_ids = [(0, 0, {"activity_id": self.ship.id, "quantity": 1700})]
        whatif.action_compute()
        self.assertFalse(self.env["cmc.budget.line"].search([("abb_run_id", "=", whatif.id)]))
        self.assertEqual(len(self.bud27.budget_line_ids), 48)
        self.assertEqual(Rate.search_count(rate_domain), rates)
