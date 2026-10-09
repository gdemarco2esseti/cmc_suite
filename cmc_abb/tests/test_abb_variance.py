"""WP-12 / STD-05 - ABC variance: budgeted resource expense vs assigned (actual driver x standard rate),
spending variance, unexpected utilisation variance, unused capacity - reconciling (Kaplan & Cooper Ch.7:
212,800 + 56,000 + 11,200 = 280,000; Ch.14 two-tier); three-way actual / ABC / ABB and idle trend (Frost
St.6-8, Appx F). CAP-05 idle trend.

CONTRACT (see common.py for the whole WP)
- ``cmc.abb.variance.run`` (cmc.run.mixin): ``company_id``, ``abb_run_id`` (computed; usage recorded),
  ``date_from`` / ``date_to`` inside the ABB span (else UserError); f = months of span / ABB months.
- ``line_ids`` -> ``cmc.abb.variance.line`` per resource: ``resource_id``, ``center_id``, ``element_id``,
  ``spending_profile``, ``static_budget_cost``, ``budget_cost``, ``actual_cost``, ``budget_demand_qty``,
  ``actual_demand_qty``, ``supply_qty``, ``rate``, ``assigned_cost``, ``budgeted_unused_cost``,
  ``utilisation_variance``, ``spending_variance``, ``abc_variance``.
  actual_demand = Σ ACTUAL facts of each activity driver in the span x usage_per_unit; actual_cost = ledger
  pool of the resource (cmc_abc rule) in the span. Committed: budget = static = ABB cost x f, supply and
  budget demand x f; flexible: budget = rate x actual demand, static = ABB cost x f.
  assigned = actual demand x rate; budgeted_unused = (supply - budget demand) x rate; utilisation =
  (actual - budget demand) x rate; spending = budget - actual; abc_variance = assigned - actual.
  Identity: assigned + budgeted_unused - utilisation = budget; actual = budget - spending.
- Totals on the header; ``_cmc_three_way(groupby)``; ``_cmc_idle_trend(resources, date_from, date_to)``.
- Snapshot: a posting after Compute changes nothing until reset + compute (AD-113).

FIXTURE (2027, on the main ABB run): actual facts SETUP 25, MACH 1,300 h, SHIP 800; ledger Z SAL 99,000,
Z DEP 30,000, Z ENE 2,700 (variable), L TRK 48,000; distractors outside the span / planned.
  TECH: demand 125 + 1,300 + 200 = 1,625 h, assigned 48,750, budgeted unused 43,200, utilisation -4,050,
  spending 96,000 - 99,000 = -3,000, abc -50,250.  MACHR: 1,300 h, 19,500, 9,000, -1,500, 0, -10,500.
  ENERGY (flexible): 13,000 kWh, budget 2,600 (static 2,800), assigned 2,600, spending -100.
  TRUCK: 800, 25,000, 22,500, -2,500, +2,000, -23,000.
  Totals: budget 178,600, actual 179,700, assigned 95,850, budgeted unused 74,700, utilisation -8,050,
  spending -1,100: 95,850 + 74,700 + 8,050 + 1,100 = 179,700.
"""

from datetime import date

from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .common import D_MID, Y27_FROM, Y27_TO, AbbCase


@tagged("post_install", "-at_install")
class TestAbbVariance(AbbCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env_a
        for driver, qty in ((cls.setups, 25), (cls.mhours, 1300), (cls.shipments, 800)):
            cls._fact(env, driver, qty, cost_object=cls.obj_p1)
        cls._fact(env, cls.mhours, 7777, cost_object=cls.obj_p1, day=date(2028, 1, 15))  # outside the span
        cls._cost(env, cls.z, 99000, cls.sal, "fixed")
        cls._cost(env, cls.z, 30000, cls.dep, "fixed")
        cls._cost(env, cls.z, 2700, cls.ene, "variable")
        cls._cost(env, cls.lg, 48000, cls.trk, "fixed")
        cls._cost(env, cls.z, 777, cls.sal, "fixed", day=date(2028, 1, 10))  # outside the span

    def _variance(self, abb, date_from=Y27_FROM, date_to=Y27_TO, compute=True, env=None):
        run = (env or self.env_a)["cmc.abb.variance.run"].create({
            "name": "WP12 variance", "company_id": self.company_a.id, "abb_run_id": abb.id,
            "date_from": date_from, "date_to": date_to,
        })
        if compute:
            run.action_compute()
        return run

    @staticmethod
    def _line(run, resource):
        return run.line_ids.filtered(lambda ln: ln.resource_id == resource)

    def test_std05_committed_resource_three_way_split(self):
        # STD-05 / Kaplan Ch.7: budget = assigned + budgeted unused + unexpected utilisation
        run = self._variance(self._abb_run())
        self.assertRecordValues(self._line(run, self.tech), [{
            "center_id": self.z.id, "element_id": self.sal.id, "budget_cost": 96000.0, "static_budget_cost": 96000.0,
            "actual_cost": 99000.0, "budget_demand_qty": 1760.0, "actual_demand_qty": 1625.0, "supply_qty": 3200.0,
            "rate": 30.0, "assigned_cost": 48750.0, "budgeted_unused_cost": 43200.0, "utilisation_variance": -4050.0,
            "spending_variance": -3000.0, "abc_variance": -50250.0,
        }])
        self.assertRecordValues(self._line(run, self.truck), [{
            "assigned_cost": 25000.0, "budgeted_unused_cost": 22500.0, "utilisation_variance": -2500.0,
            "spending_variance": 2000.0, "abc_variance": -23000.0,
        }])
        self.assertRecordValues(self._line(run, self.machr), [{
            "assigned_cost": 19500.0, "budgeted_unused_cost": 9000.0, "utilisation_variance": -1500.0, "spending_variance": 0.0,
        }])

    def test_std05_flexible_resource_two_tier(self):
        # STD-05 / Kaplan Ch.14: a flexible resource is budgeted at actual usage - no capacity variance
        run = self._variance(self._abb_run())
        self.assertRecordValues(self._line(run, self.energy), [{
            "spending_profile": "flexible", "actual_demand_qty": 13000.0, "budget_cost": 2600.0, "static_budget_cost": 2800.0,
            "assigned_cost": 2600.0, "budgeted_unused_cost": 0.0, "utilisation_variance": 0.0, "spending_variance": -100.0,
            "actual_cost": 2700.0,
        }])

    def test_std05_every_line_reconciles(self):
        # STD-05: the split must add up line by line, or the report cannot be trusted
        run = self._variance(self._abb_run())
        self.assertEqual(len(run.line_ids), 4)
        for line in run.line_ids:
            self.assertAlmostEqual(line.assigned_cost + line.budgeted_unused_cost - line.utilisation_variance,
                                   line.budget_cost, places=2, msg=line.resource_id.name)
            self.assertAlmostEqual(line.budget_cost - line.spending_variance, line.actual_cost, places=2,
                                   msg=line.resource_id.name)

    def test_std05_totals(self):
        # STD-05: company totals of the variance report
        run = self._variance(self._abb_run())
        self.assertRecordValues(run, [{
            "total_budget_cost": 178600.0, "total_actual_cost": 179700.0, "total_assigned_cost": 95850.0,
            "total_budgeted_unused_cost": 74700.0, "total_utilisation_variance": -8050.0, "total_spending_variance": -1100.0,
        }])

    def test_std05_three_way_by_department(self):
        # STD-05 / Frost St.8: actual vs ABC vs ABB per department, with company roll-up
        run = self._variance(self._abb_run())
        rows = {row["center_id"]: row for row in run._cmc_three_way(groupby=("center_id",))}
        self.assertEqual(set(rows), {self.z, self.lg})
        self.assertAlmostEqual(rows[self.z]["actual_cost"], 131700.0, places=2)
        self.assertAlmostEqual(rows[self.z]["abc_cost"], 70850.0, places=2)
        self.assertAlmostEqual(rows[self.z]["abb_cost"], 128800.0, places=2)  # static ABB plan (energy 2,800)
        self.assertAlmostEqual(rows[self.z]["abc_variance"], 70850.0 - 131700.0, places=2)
        self.assertAlmostEqual(rows[self.lg]["actual_cost"], 48000.0, places=2)
        (total,) = run._cmc_three_way(groupby=())
        self.assertAlmostEqual(total["actual_cost"], 179700.0, places=2)
        self.assertAlmostEqual(total["abb_cost"], 178800.0, places=2)

    def test_std05_snapshot(self):
        # AD-113: a late posting does not move a computed variance
        run = self._variance(self._abb_run())
        self._cost(self.env_a, self.z, 1000, self.sal, "fixed", day=D_MID)
        self._fact(self.env_a, self.mhours, 100, cost_object=self.obj_p2)
        self.assertAlmostEqual(self._line(run, self.tech).actual_cost, 99000.0, places=2)
        self.assertAlmostEqual(self._line(run, self.tech).actual_demand_qty, 1625.0, places=4)

    def test_std05_needs_computed_abb_and_span_inside(self):
        # STD-05 / AD-002: the reference budget must exist and cover the period
        draft = self._abb_run(compute=False)
        with self.assertRaises(UserError):
            self._variance(draft)
        abb = self._abb_run()
        with self.assertRaises(UserError):
            self._variance(abb, date_from=date(2027, 12, 1), date_to=date(2028, 1, 31))

    def test_std05_abb_locked_while_variance_computed(self):
        # AD-002: the budget a computed variance reads cannot be reset under it
        abb = self._abb_run()
        variance = self._variance(abb)
        with self.assertRaises(UserError):
            abb.action_reset_to_draft()
        variance.action_reset_to_draft()
        self.assertFalse(variance.line_ids)
        abb.action_reset_to_draft()
        self.assertEqual(abb.state, "draft")

    def test_cap05_idle_trend(self):
        # CAP-05 / Frost St.8: idle cost by resource over time (Jan 100 h, Feb 150 h of machine work)
        abb = self._abb_run()
        self._fact(self.env_a, self.mhours, 100, cost_object=self.obj_p2, day=date(2027, 1, 15))
        self._fact(self.env_a, self.mhours, 150, cost_object=self.obj_p2, day=date(2027, 2, 15))
        jan = self._variance(abb, date(2027, 1, 1), date(2027, 1, 31))
        self._variance(abb, date(2027, 2, 1), date(2027, 2, 28))
        self._variance(abb, date(2027, 3, 1), date(2027, 3, 31), compute=False)  # draft: not in the trend
        tech_jan = self._line(jan, self.tech)
        self.assertAlmostEqual(tech_jan.budget_cost, 8000.0, places=2)  # 96,000 / 12
        self.assertAlmostEqual(tech_jan.assigned_cost, 3000.0, places=2)  # 100 h x 30
        trend = self.env["cmc.abb.variance.run"]._cmc_idle_trend(self.tech, date(2027, 1, 1), date(2027, 12, 31))
        self.assertEqual([(row["month"], row["resource"]) for row in trend],
                         [(date(2027, 1, 1), self.tech), (date(2027, 2, 1), self.tech)])
        self.assertAlmostEqual(trend[0]["unused_cost"], 5000.0, places=2)
        self.assertAlmostEqual(trend[0]["unused_percent"], 62.5, places=2)
        self.assertAlmostEqual(trend[1]["unused_cost"], 3500.0, places=2)
        self.assertAlmostEqual(trend[1]["unused_percent"], 43.75, places=2)
