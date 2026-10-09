"""WP-12 / BUD-06 - activity-based budgeting south->north and north->south (Kaplan & Cooper Ch.15, Cokins
Exec Ch.2/8 ABB/P, Frost St.8 FTE).

CONTRACT (see common.py for the whole WP)
- Planned volume = PLANNED facts of ``output_measure_id`` in the source version (grouped, all months);
  activity demand = Σ volume x ``qty_per_unit``; resource demand = Σ activity demand x ``usage_per_unit``.
- Supply by spending profile, FTE (``required_units``), shortage / excess vs ``committed_units``,
  ``budgeted_cost``, resource rate; activity rates (fixed / variable), assigned cost, object unit cost.
- Target version: one ``_cmc_budget_add`` per resource (12 lines, ``abb_run_id`` / ``abb_resource_id``),
  center x element x behavior (committed fixed, flexible variable); never analytic lines (AD-110).
- Prospective rates in ``cmc.rate`` with origin = target version, every month of the span.
- Extrapolation warnings vs the calibration ABC run (volume per activity, mix per new object).
- ``action_prefill_bill()``: bill of activities from the calibration run edges / produced quantity.
- Conservation: total_budgeted_cost = total_assigned_cost + total_unused_cost.
"""

from datetime import date

from odoo.tests.common import tagged

from .common import D_MID, AbbCase


@tagged("post_install", "-at_install")
class TestAbbEngine(AbbCase):
    def test_bud06_activity_demand_from_planned_volume(self):
        # BUD-06 step 1-2: forecast volume x bill of activities = activity driver demand (planned facts only)
        run = self._abb_run()
        self.assertAlmostEqual(self._act(run, self.setup).demand_qty, 28.0, places=4)
        self.assertAlmostEqual(self._act(run, self.mach).demand_qty, 1400.0, places=4)
        self.assertAlmostEqual(self._act(run, self.ship).demand_qty, 880.0, places=4)
        self.assertEqual(self._act(run, self.mach).driver_id, self.mhours)
        # the distractor actual fact P1 5,555 and the planned machine-hour fact 9,999 never enter
        self.assertAlmostEqual(self._obj(run, self.obj_p1).planned_qty, 1200.0, places=4)
        self.assertAlmostEqual(self._obj(run, self.obj_p2).planned_qty, 800.0, places=4)

    def test_bud06_resource_demand_and_fte(self):
        # BUD-06 step 3 / Frost St.8: resource demand = Σ driver qty x unit time; FTE = hours / annual hours
        run = self._abb_run()
        tech = self._res(run, self.tech)
        self.assertAlmostEqual(tech.demand_qty, 1760.0, places=4)  # 28 x 5 + 1,400 x 1 + 880 x 0.25
        self.assertAlmostEqual(tech.required_units, 1.1, places=4)
        self.assertAlmostEqual(self._res(run, self.energy).demand_qty, 14000.0, places=4)
        self.assertAlmostEqual(self._res(run, self.truck).demand_qty, 880.0, places=4)

    def test_bud06_supply_shortage_excess(self):
        # BUD-06 step 4 / Kaplan Ch.15: supply by spending profile, then excess (redeploy) or shortage (spend)
        run = self._abb_run()
        self.assertRecordValues(self._res(run, self.tech), [{
            "spending_profile": "committed_step", "supply_units": 2.0, "supply_qty": 3200.0, "unused_qty": 1440.0,
            "committed_units": 3.0, "shortage_units": 0.0, "excess_units": 1.0, "budgeted_cost": 96000.0,
        }])
        self.assertRecordValues(self._res(run, self.truck), [{
            "spending_profile": "committed_step", "supply_units": 2.0, "supply_qty": 1600.0, "unused_qty": 720.0,
            "committed_units": 1.0, "shortage_units": 1.0, "excess_units": 0.0, "budgeted_cost": 50000.0,
        }])
        self.assertRecordValues(self._res(run, self.machr), [{
            "spending_profile": "committed_fixed", "supply_units": 1.0, "supply_qty": 2000.0, "unused_qty": 600.0,
            "shortage_units": 0.0, "excess_units": 0.0, "budgeted_cost": 30000.0,
        }])
        self.assertRecordValues(self._res(run, self.energy), [{
            "spending_profile": "flexible", "supply_qty": 14000.0, "unused_qty": 0.0, "budgeted_cost": 2800.0,
        }])

    def test_bud06_prospective_rates_north_to_south(self):
        # BUD-06 / Kaplan Ch.15: after the budget, rates run north->south on the capacity supplied
        run = self._abb_run()
        self.assertAlmostEqual(self._res(run, self.tech).rate, 30.0, places=4)
        self.assertAlmostEqual(self._res(run, self.truck).rate, 31.25, places=4)
        self.assertRecordValues(self._act(run, self.mach), [{"rate": 47.0, "fixed_rate": 45.0, "variable_rate": 2.0,
                                                             "assigned_cost": 65800.0}])
        self.assertRecordValues(self._act(run, self.setup), [{"rate": 150.0, "assigned_cost": 4200.0}])
        self.assertRecordValues(self._act(run, self.ship), [{"rate": 38.75, "assigned_cost": 34100.0}])

    def test_bud06_object_unit_cost(self):
        # BUD-06: pro-forma ABC of each product = bill of activities x prospective rates
        run = self._abb_run()
        self.assertRecordValues(self._obj(run, self.obj_p1), [{"unit_cost": 40.5, "amount": 48600.0}])
        self.assertRecordValues(self._obj(run, self.obj_p2), [{"unit_cost": 69.375, "amount": 55500.0}])

    def test_bud06_conservation(self):
        # Kaplan Ch.7 fundamental equation: resources supplied = resources used + unused capacity
        run = self._abb_run()
        self.assertAlmostEqual(run.total_budgeted_cost, 178800.0, places=2)
        self.assertAlmostEqual(run.total_assigned_cost, 104100.0, places=2)
        self.assertAlmostEqual(run.total_unused_cost, 74700.0, places=2)
        self.assertAlmostEqual(sum(run.resource_result_ids.mapped("unused_cost")), 74700.0, places=2)
        self.assertAlmostEqual(sum(run.object_result_ids.mapped("amount")), run.total_assigned_cost, places=2)

    def test_bud06_budget_lines_in_target_version(self):
        # BUD-06: ABB output in classic budget format (center x element x behavior), monthly (AD-105)
        run = self._abb_run()
        lines = self.env["cmc.budget.line"].search([("abb_run_id", "=", run.id)])
        self.assertEqual(len(lines), 48)  # 4 resources x 12 months
        self.assertEqual(lines.version_id, self.bud27)
        tech = lines.filtered(lambda ln: ln.abb_resource_id == self.tech)
        self.assertEqual(len(tech), 12)
        self.assertEqual(set(tech.mapped("behavior")), {"fixed"})
        self.assertEqual(tech.center_id, self.z)
        self.assertEqual(tech.element_id, self.sal)
        self.assertEqual(tech.driver_id, self.lhours)
        self.assertAlmostEqual(sum(tech.mapped("figure")), 96000.0, places=2)
        energy = lines.filtered(lambda ln: ln.abb_resource_id == self.energy)
        self.assertEqual(set(energy.mapped("behavior")), {"variable"})
        budget = self.bud27._cmc_center_budget()
        self.assertAlmostEqual(budget[self.z.id]["fixed"], 126000.0, places=2)
        self.assertAlmostEqual(budget[self.z.id]["variable"], 2800.0, places=2)
        self.assertAlmostEqual(budget[self.lg.id]["total"], 50000.0, places=2)

    def test_bud06_never_analytic_lines(self):
        # AD-110: a plan figure written as an analytic line would be read as an actual in 5 places
        Line = self.env["account.analytic.line"].sudo()
        before = Line.search_count([("company_id", "=", self.company_a.id)])
        self._abb_run()
        self.assertEqual(Line.search_count([("company_id", "=", self.company_a.id)]), before)

    def test_bud06_rates_published_on_target_version(self):
        # AD-108: prospective rates are PLANNED rates (origin = the target version), valid every month
        run = self._abb_run()
        Rate = self.env["cmc.rate"]

        def rate(holder, driver, component="total", day=D_MID):
            return Rate._cmc_lookup(holder, driver, day, origin=self.bud27, component=component, company=self.company_a)

        self.assertAlmostEqual(rate(self.mach, self.mhours).value, 47.0, places=4)
        self.assertAlmostEqual(rate(self.mach, self.mhours, "fixed").value, 45.0, places=4)
        self.assertAlmostEqual(rate(self.mach, self.mhours, "variable").value, 2.0, places=4)
        self.assertAlmostEqual(rate(self.ship, self.shipments, day=date(2027, 12, 1)).value, 38.75, places=4)
        self.assertAlmostEqual(rate(self.tech, self.lhours).value, 30.0, places=4)
        self.assertAlmostEqual(rate(self.obj_p2, self.units).value, 69.375, places=4)
        self.assertEqual(len(Rate.search([("origin_model", "=", "cmc.plan.version"), ("origin_id", "=", self.bud27.id),
                                          ("holder_model", "=", "cmc.abc.activity"), ("holder_id", "=", self.setup.id),
                                          ("component", "=", "total")])), 12)
        # planned rates are never served as actual rates
        self.assertFalse(Rate._cmc_lookup(self.mach, self.mhours, D_MID, company=self.company_a))
        self.assertEqual(run.state, "done")

    def test_bud06_no_target_writes_nothing(self):
        # BUD-06: without a target version the run is an analysis only
        run = self._abb_run(target_version_id=False)
        self.assertAlmostEqual(run.total_budgeted_cost, 178800.0, places=2)
        self.assertFalse(self.env["cmc.budget.line"].search([("abb_run_id", "=", run.id)]))
        self.assertFalse(self.env["cmc.rate"].search([("origin_model", "=", "cmc.plan.version"), ("origin_id", "=", self.bud27.id)]))

    def test_bud06_no_warning_inside_calibration_range(self):
        # BUD-06: -6.67% / -6.67% / -2.22% vs calibration (30 / 1,500 / 900) is inside the 20% tolerance
        run = self._abb_run()
        self.assertFalse(run.extrapolation_warning)
        self.assertFalse(run.warning_ids)
        self.assertRecordValues(self._act(run, self.setup), [{"calibration_qty": 30.0}])
        self.assertAlmostEqual(self._act(run, self.mach).calibration_qty, 1500.0, places=4)
        self.assertAlmostEqual(self._act(run, self.ship).deviation_percent, -2.2222, places=3)

    def test_bud06_volume_extrapolation_warning(self):
        # BUD-06: rates calibrated on 1,500 machine hours are not valid at 2,800 - warn, do not block
        version = self._version(self.env_a, "WP12 FC27 high", "forecast")
        self._fact(self.env_a, self.units, 4000, cost_object=self.obj_p1, day=date(2027, 1, 15), version=version)
        self._fact(self.env_a, self.units, 800, cost_object=self.obj_p2, day=date(2027, 1, 15), version=version)
        version.with_user(self.manager_a).action_approve()
        run = self._abb_run(source_version_id=version.id, target_version_id=False)
        self.assertTrue(run.extrapolation_warning)
        volume = run.warning_ids.filtered(lambda w: w.warning_type == "volume")
        self.assertEqual(volume.activity_id, self.setup | self.mach | self.ship)
        ship = volume.filtered(lambda w: w.activity_id == self.ship)
        self.assertAlmostEqual(ship.planned_qty, 2000.0, places=4)
        self.assertAlmostEqual(ship.calibration_qty, 900.0, places=4)
        self.assertAlmostEqual(ship.deviation_percent, 122.2222, places=3)

    def test_bud06_mix_extrapolation_warning(self):
        # BUD-06: a product the calibration never costed is outside the calibrated mix
        version = self._version(self.env_a, "WP12 FC27 mix", "forecast")
        for obj, qty in ((self.obj_p1, 1200), (self.obj_p2, 800), (self.obj_p3, 100)):
            self._fact(self.env_a, self.units, qty, cost_object=obj, day=date(2027, 2, 15), version=version)
        version.with_user(self.manager_a).action_approve()
        bill = self._bill() + [{"cost_object_id": self.obj_p3.id, "activity_id": self.mach.id, "qty_per_unit": 0.5}]
        run = self._abb_run(source_version_id=version.id, target_version_id=False, bill=bill)
        self.assertTrue(run.extrapolation_warning)
        self.assertRecordValues(run.warning_ids, [{"warning_type": "mix", "cost_object_id": self.obj_p3.id}])
        self.assertAlmostEqual(self._act(run, self.mach).demand_qty, 1450.0, places=4)

    def test_bud06_prefill_bill_from_calibration(self):
        # BUD-06 / Cokins Exec Ch.2: the bill of activities comes from the calibrated ABC model
        run = self._abb_run(compute=False, bill=[])
        run.action_prefill_bill()
        got = {(ln.cost_object_id, ln.activity_id): ln.qty_per_unit for ln in run.bill_line_ids}
        expected = {(self.env["cmc.cost.object"].browse(v["cost_object_id"]), self.env["cmc.abc.activity"].browse(v["activity_id"])):
                    v["qty_per_unit"] for v in self._bill()}
        self.assertEqual(set(got), set(expected))
        for key, qty in expected.items():
            self.assertAlmostEqual(got[key], qty, places=6, msg=key)
        run.action_compute()
        self.assertAlmostEqual(run.total_budgeted_cost, 178800.0, places=2)
