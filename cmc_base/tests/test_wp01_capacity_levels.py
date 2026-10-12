"""WP-01 / CAP-01 - five capacity levels per center (theoretical, practical, normal, budget, actual).

CONTRACT (``cmc.capacity.analysis``)
- New inputs ``capacity_normal`` and ``capacity_budget`` (Float, >= 0, each <= ``capacity_theoretical`` when
  the theoretical is set: ValidationError or SQL CHECK), ``downtime_rate`` (Float, percent of theoretical lost
  to breaks/downtime, 0..100).
- ``action_estimate_practical()``: practical = theoretical x (1 - downtime_rate / 100).
- New derived stored fields: ``total_cost`` = CF + CV = cost of ALL cost-base lines of the center in the period
  (any behavior, plan-aware), and the cost coefficient per level ``coefficient_theoretical``,
  ``coefficient_practical``, ``coefficient_normal``, ``coefficient_budget``, ``coefficient_actual`` =
  total_cost / capacity of that level (0 when the capacity is 0). Kept in sync when a line of the center is
  booked, whatever its behavior (the existing hook only follows fixed lines).
- Existing fixed-cost figures (fixed_cost, coefficient, imputed, unused, utilisation) are unchanged.

Hand-computed (register example): CF 18000 + CV 12000 = 30000; capacities 12000 / 11400 / 10600 / 10300 /
10000 -> coefficients 2.50 / 2.63 / 2.83 / 2.91 / 3.00. Practical from 12000 with 5% downtime = 11400.
"""

from datetime import date

from psycopg2 import IntegrityError

from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, tagged
from odoo.tools import mute_logger


@tagged("post_install", "-at_install")
class TestCapacityLevels(TransactionCase):
    """CAP-01: the unit cost depends on the capacity level chosen as denominator."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.AL = cls.env["account.analytic.line"]
        cls.CA = cls.env["cmc.capacity.analysis"]
        plan = cls.env["account.analytic.plan"].search([("parent_id", "=", False)]).filtered(
            lambda p: p._column_name() == "account_id"
        )[:1]
        cls.center = cls.env["account.analytic.account"].create(
            {"name": "WP01 Cap press", "plan_id": plan.id, "cmc_center_type": "intermediate"}
        )
        cls.AL.create({"name": "fixed", "account_id": cls.center.id, "amount": -18000.0,
                       "date": date(2031, 2, 10), "cmc_behavior": "fixed"})
        cls.AL.create({"name": "variable", "account_id": cls.center.id, "amount": -12000.0,
                       "date": date(2031, 2, 11), "cmc_behavior": "variable"})

    def _analysis(self, **kw):
        vals = {"center_id": self.center.id, "date_from": date(2031, 1, 1), "date_to": date(2031, 12, 31),
                "capacity_theoretical": 12000.0, "capacity_practical": 11400.0, "capacity_normal": 10600.0,
                "capacity_budget": 10300.0, "capacity_actual": 10000.0}
        vals.update(kw)
        return self.CA.create(vals)

    def test_five_level_coefficients(self):
        # CAP-01: coeff per level = (CF + CV) / capacity
        ca = self._analysis()
        self.assertAlmostEqual(ca.total_cost, 30000.0)
        self.assertAlmostEqual(ca.coefficient_theoretical, 2.50, places=2)
        self.assertAlmostEqual(ca.coefficient_practical, 2.63, places=2)
        self.assertAlmostEqual(ca.coefficient_normal, 2.83, places=2)
        self.assertAlmostEqual(ca.coefficient_budget, 2.91, places=2)
        self.assertAlmostEqual(ca.coefficient_actual, 3.00, places=2)

    def test_fixed_cost_figures_unchanged(self):
        # CAP-01 must not alter the existing fixed-cost / unused-capacity figures
        ca = self._analysis()
        self.assertAlmostEqual(ca.fixed_cost, 18000.0)
        self.assertAlmostEqual(ca.unused_capacity_cost, 18000.0 - round(18000.0 / 11400.0 * 10000.0, 2), places=2)

    def test_zero_capacity_gives_zero_coefficient(self):
        # CAP-01: a level left empty has no coefficient (no division by zero)
        ca = self._analysis(capacity_normal=0.0, capacity_budget=0.0)
        self.assertEqual(ca.coefficient_normal, 0.0)
        self.assertEqual(ca.coefficient_budget, 0.0)

    def test_estimate_practical_from_downtime(self):
        # CAP-01: practical = theoretical x (1 - breaks/downtime)
        ca = self._analysis(capacity_practical=0.0, downtime_rate=5.0)
        ca.action_estimate_practical()
        self.assertAlmostEqual(ca.capacity_practical, 11400.0)
        self.assertAlmostEqual(ca.coefficient_practical, 2.63, places=2)

    def test_total_cost_follows_variable_lines(self):
        # CAP-01: CF + CV stays in sync when a variable line is booked later
        ca = self._analysis()
        self.AL.create({"name": "variable late", "account_id": self.center.id, "amount": -3000.0,
                        "date": date(2031, 3, 1), "cmc_behavior": "variable"})
        self.assertAlmostEqual(ca.total_cost, 33000.0)
        self.assertAlmostEqual(ca.coefficient_actual, 3.30, places=2)

    @mute_logger("odoo.sql_db")
    def test_normal_cannot_exceed_theoretical(self):
        # CAP-01: normal and budget levels are bounded by the theoretical capacity
        for vals in ({"capacity_normal": 13000.0}, {"capacity_budget": 12500.0}):
            try:
                with self.env.cr.savepoint():
                    self._analysis(**vals)
                    self.env.flush_all()
            except (ValidationError, IntegrityError):
                continue
            self.fail(f"capacity level above theoretical accepted: {vals}")
