"""STD-02 / STD-03 / CAP-04 - flexible budget, spending/efficiency/volume variances, capacity reconciliation.

Hand-computed (March 2033, center M, driver machine hours):
budget rent fixed 10,000 and energy variable 20,000 on 1,000 planned h -> v = 20/h, f = 10/h.
Actual 1,100 h, allowed for the actual output 1,050 h; actual rent 10,400, energy 23,000.
  rent:   flexible -10,000; spending -400;   efficiency 0;      volume 10 x (1,050 - 1,000) = +500
  energy: flexible -(20 x 1,100) = -22,000; spending -1,000; efficiency 20 x (1,050 - 1,100) = -1,000
  identity: actual -33,400 + standard applied 30 x 1,050 = -1,900 = -1,400 - 1,000 + 500.
Without allowed quantity: efficiency 0, rent volume 10 x (1,100 - 1,000) = +1,000.
Mix (STD-02): rent 5,000 (no driver) + energy 20/mh x 900 + labour 30/lh x 600 -> flexible 41,000.
CAP-04 (Kaplan Ch.7): fixed budget 280,000, practical 5,000, planned 4,000, actual 3,800, actual cost 273,600:
rate 56, assigned -212,800, unused budgeted -56,000, unexpected -11,200 (sum -280,000), spending +6,400.
RN-002 (BUD-03 x STD-02): 2033-2034 plan, energy variable 1,000/yr (March) on mh, planned 100 h/yr; March 2033
actual 50 h, cost 500 -> v 10, flexible -500, spending 0 on the parent AND on the 2033 child of the split.
"""

from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .common import Y, CmcBudgetCase, d

MARCH = {"date_from": d(3), "date_to": d(3, 31)}


@tagged("post_install", "-at_install")
class TestBudgetVariance(CmcBudgetCase):
    """STD-03: the spending variance is computed per center and element and stays in that center."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Run = cls.env["cmc.budget.variance.run"]
        cls.center = cls._center("BUD var M")
        cls.version = cls._version(name="BUD var 2033")
        cls._line(cls.version, cls.center, 3, 10000.0, element=cls.rent, behavior="fixed", driver_id=cls.mh.id)
        cls._line(cls.version, cls.center, 3, 20000.0, element=cls.energy, behavior="variable", driver_id=cls.mh.id)
        cls._qty(cls.mh, cls.center, 3, 1000.0, version=cls.version)
        cls._qty(cls.mh, cls.center, 3, 1100.0)
        cls._cost(cls.center, 10400.0, d(3, 10), element=cls.rent)
        cls._cost(cls.center, 23000.0, d(3, 12), element=cls.energy, behavior="variable")
        cls.version.with_user(cls.manager).action_approve()

    def _run(self, version=None, allowed=None, **vals):
        run = self.Run.with_user(self.manager).create({
            "name": "BUD variance", "version_id": (version or self.version).id, **MARCH, **vals,
            "input_ids": [(0, 0, {"center_id": center.id, "driver_id": driver.id, "allowed_qty": qty})
                          for center, driver, qty in (allowed or [])],
        })
        run.action_compute()
        return run.sudo()

    def _by_element(self, run, center=None):
        lines = run.line_ids.filtered(lambda line: line.center_id == (center or self.center))
        return {line.element_id: line for line in lines}

    def test_flexible_budget_at_actual_volume(self):
        # STD-02: allowed = fixed budget + v x actual quantity
        lines = self._by_element(self._run())
        self.assertRecordValues(lines[self.rent], [{"budget_fixed": -10000.0, "budget_variable": 0.0, "flexible_amount": -10000.0,
                                                    "planned_qty": 1000.0, "actual_qty": 1100.0}])
        self.assertRecordValues(lines[self.energy], [{"budget_fixed": 0.0, "budget_variable": -20000.0, "flexible_amount": -22000.0,
                                                      "planned_qty": 1000.0, "actual_qty": 1100.0}])

    def test_spending_variance_per_element_stays_in_center(self):
        # STD-03: var = actual - authorized, unfavourable is negative (AD-112)
        lines = self._by_element(self._run())
        self.assertRecordValues(lines[self.rent], [{"actual_amount": -10400.0, "spending_variance": -400.0}])
        self.assertRecordValues(lines[self.energy], [{"actual_amount": -23000.0, "spending_variance": -1000.0}])

    def test_efficiency_and_volume_variance_on_allowed_quantity(self):
        # STD-03: efficiency on the variable rate, volume on the fixed rate, both on the allowed quantity
        lines = self._by_element(self._run(allowed=[(self.center, self.mh, 1050.0)]))
        self.assertRecordValues(lines[self.energy], [{"allowed_qty": 1050.0, "efficiency_variance": -1000.0, "volume_variance": 0.0}])
        self.assertRecordValues(lines[self.rent], [{"allowed_qty": 1050.0, "efficiency_variance": 0.0, "volume_variance": 500.0}])

    def test_variances_reconcile_to_standard_cost_applied(self):
        # STD-03: actual - standard applied = spending + efficiency + volume
        run = self._run(allowed=[(self.center, self.mh, 1050.0)])
        total = sum(run.line_ids.mapped(lambda line: line.spending_variance + line.efficiency_variance + line.volume_variance))
        self.assertAlmostEqual(total, -1900.0)
        self.assertAlmostEqual(sum(run.line_ids.mapped("actual_amount")) + 30.0 * 1050.0, total)

    def test_allowed_defaults_to_actual_quantity(self):
        # STD-03: without an allowed quantity there is no efficiency variance
        lines = self._by_element(self._run())
        self.assertRecordValues(lines[self.energy], [{"allowed_qty": 1100.0, "efficiency_variance": 0.0}])
        self.assertRecordValues(lines[self.rent], [{"volume_variance": 1000.0}])

    def test_flexible_budget_at_actual_mix_of_drivers(self):
        # STD-02: allowed = K + sum of each standard rate x its actual quantity
        center = self._center("BUD var mix")
        version = self._version(name="BUD var mix")
        self._line(version, center, 3, 5000.0, element=self.rent)
        self._line(version, center, 3, 20000.0, element=self.energy, behavior="variable", driver_id=self.mh.id)
        self._line(version, center, 3, 15000.0, element=self.labour, behavior="variable", driver_id=self.lh.id)
        self._qty(self.mh, center, 3, 1000.0, version=version)
        self._qty(self.lh, center, 3, 500.0, version=version)
        self._qty(self.mh, center, 3, 900.0)
        self._qty(self.lh, center, 3, 600.0)
        self._approve(version)
        lines = self._by_element(self._run(version=version), center=center)
        self.assertAlmostEqual(lines[self.energy].flexible_amount, -18000.0)
        self.assertAlmostEqual(lines[self.labour].flexible_amount, -18000.0)
        self.assertAlmostEqual(sum(line.flexible_amount for line in lines.values()), -41000.0)

    def test_capacity_three_way_reconciliation(self):
        # CAP-04: assigned + unused budgeted + unexpected utilisation = budget; spending = actual - budget
        orders = self.env["cmc.cost.driver"].create({"name": "BUD orders", "code": "BUDORD"})
        center = self._center("BUD var purchasing")
        version = self._version(name="BUD var capacity")
        self._line(version, center, 3, 280000.0, element=self.rent, driver_id=orders.id)
        self._qty(orders, center, 3, 4000.0, version=version)
        self._qty(orders, center, 3, 3800.0)
        self._cost(center, 273600.0, d(3, 15), element=self.rent)
        self.env["cmc.capacity.analysis"].create({"center_id": center.id, "driver_id": orders.id, **MARCH,
                                                  "capacity_practical": 5000.0})
        self._approve(version)
        line = self._by_element(self._run(version=version), center=center)[self.rent]
        self.assertRecordValues(line, [{
            "practical_qty": 5000.0, "capacity_rate": 56.0, "capacity_assigned": -212800.0,
            "capacity_unused_budgeted": -56000.0, "capacity_unused_unexpected": -11200.0, "spending_variance": 6400.0,
        }])
        self.assertAlmostEqual(line.capacity_assigned + line.capacity_unused_budgeted + line.capacity_unused_unexpected, line.budget_fixed)

    def test_capacity_fields_zero_without_practical_capacity(self):
        # CAP-04: no practical capacity typed, no capacity split (never a division by zero)
        line = self._by_element(self._run())[self.rent]
        self.assertRecordValues(line, [{"practical_qty": 0.0, "capacity_rate": 0.0, "capacity_assigned": 0.0}])

    def test_draft_version_refused(self):
        # AD-113: a computed run only reads an approved version
        version = self._version(name="BUD var draft")
        self._line(version, self.center, 3, 100.0)
        with self.assertRaises(UserError):
            self._run(version=version)

    def test_run_is_a_snapshot(self):
        # AD-113: a later posting changes nothing until reset and compute
        run = self._run()
        self._cost(self.center, 100.0, d(3, 20), element=self.rent)
        self.assertAlmostEqual(self._by_element(run)[self.rent].actual_amount, -10400.0)
        run.with_user(self.manager).action_reset_to_draft()
        self.assertFalse(run.line_ids)
        run.with_user(self.manager).action_compute()
        self.assertAlmostEqual(self._by_element(run)[self.rent].actual_amount, -10500.0)

    def test_version_cannot_be_reset_while_variance_computed(self):
        # AD-002: the version read by a computed variance run is locked
        run = self._run()
        with self.assertRaises(UserError):
            self.version.with_user(self.manager).action_reset_to_draft()
        run.with_user(self.manager).action_reset_to_draft()
        self.version.with_user(self.manager).action_reset_to_draft()
        self.assertEqual(self.version.state, "draft")

    def test_results_written_by_compute_only(self):
        # AD-113: result rows are engine rows
        run = self._run()
        with self.assertRaises(UserError):  # AccessError subclasses UserError
            run.line_ids[:1].with_user(self.manager).write({"spending_variance": 0.0})

    def test_query_count_independent_of_volume(self):
        # AD-114: grouped reads; more centers' lines, months and postings in the version do not add queries
        def build(prefix, extra_lines):
            center = self._center(f"BUD qc {prefix}")
            version = self._version(name=f"BUD qc {prefix}")
            self._line(version, center, 3, 10000.0, element=self.rent, driver_id=self.mh.id)
            self._line(version, center, 3, 20000.0, element=self.energy, behavior="variable", driver_id=self.mh.id)
            self._qty(self.mh, center, 3, 1000.0, version=version)
            self._qty(self.mh, center, 3, 1100.0)
            for i in range(extra_lines):
                self._cost(center, 10.0, d(3, 1 + i % 28), element=self.rent)
                self._cost(center, 20.0, d(3, 1 + i % 28), element=self.energy, behavior="variable")
                self._qty(self.mh, center, 3, 1.0)
            self._approve(version)
            return version

        counts = []
        for version in (build("small", 1), build("large", 60)):
            run = self.Run.with_user(self.manager).create({"name": "qc", "version_id": version.id, **MARCH})
            self.env.invalidate_all()
            before = self.cr.sql_log_count
            run.action_compute()
            counts.append(self.cr.sql_log_count - before)
        self.assertLessEqual(counts[1], counts[0] + 2, f"query count grows with the volume: {counts}")

    def _two_year_plan(self):
        center = self._center("BUD var split")
        plan = self._version(name="BUD var plan", date_to=d(12, 31, year=Y + 1))
        for year in (Y, Y + 1):
            self.Line.create({
                "version_id": plan.id, "center_id": center.id, "element_id": self.energy.id, "behavior": "variable",
                "driver_id": self.mh.id, "date": d(3, year=year), "figure": 1000.0,
            })
            self._qty(self.mh, center, 3, 100.0, version=plan, year=year)
        self._qty(self.mh, center, 3, 50.0)
        self._cost(center, 500.0, d(3, 12), element=self.energy, behavior="variable")
        return center, plan

    def test_rn002_split_copies_planned_quantities(self):
        # RN-002 / BUD-03 / AD-107: each annual child carries the planned facts of its year
        center, plan = self._two_year_plan()
        children = plan.action_split_annual()
        for child in children:
            facts = self.Qty.search([("version_id", "=", child.id)])
            self.assertRecordValues(facts, [{
                "driver_id": self.mh.id, "center_id": center.id, "date": d(3, year=child.date_from.year), "quantity": 100.0,
            }])

    def test_rn002_child_variance_equals_parent_year(self):
        # RN-002 / STD-02 / STD-03: flexible budget and spending variance of the 2033 child = parent's 2033
        center, plan = self._two_year_plan()
        children = plan.action_split_annual()
        child = children.filtered(lambda version: version.date_from.year == Y)
        expected = [{"planned_qty": 100.0, "actual_qty": 50.0, "budget_variable": -1000.0, "flexible_amount": -500.0,
                     "actual_amount": -500.0, "spending_variance": 0.0}]
        for version in (plan, child):
            self._approve(version)
            line = self._by_element(self._run(version=version), center=center)[self.energy]
            self.assertRecordValues(line, expected)

    def test_rn002_variable_line_without_planned_quantity_refused(self):
        # RN-002 / STD-02: a variable line with no planned quantity for its driver has no rate -> UserError,
        # never a silent fallback to the static budget
        center = self._center("BUD var no qty")
        version = self._version(name="BUD var no qty")
        self._line(version, center, 3, 1000.0, element=self.energy, behavior="variable", driver_id=self.mh.id)
        self._qty(self.mh, center, 3, 50.0)  # actual only: the version has no planned mh for the center
        self._approve(version)
        self.assertEqual(version.state, "done")  # why: rules out the draft-version UserError
        with self.assertRaises(UserError):
            self._run(version=version)
