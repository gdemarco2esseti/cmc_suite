"""WP-09 DIF-05 / DC-02 - price floor check and direct-costing statement generated from the facts.

CONTRACT (part of common.py)
- DIF-05 (Turney Ch8-9): ``floor_status`` on facts (``unit_price``) and product rows (``avg_price`` = revenue /
  quantity): ``below_variable`` when price < variable unit cost (floor), ``below_full`` when variable <= price <
  full unit cost (reprice candidate), else ``ok``. Rows without product/quantity: ``ok``.
- DC-02: ``run.action_cmc_generate_direct_costing(dimension)`` on a computed run (draft -> UserError) creates
  a ``cmc.direct.costing`` of the run company and span, mode ``evoluto``, positive figures; one line per row
  of the dimension plus "Unassigned"; specific fixed = the costs of the row's own level, common fixed = the
  rest of the run costs, so ``operating_result`` = run ``operating_profit``.
"""

from datetime import date

from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .common import D_FROM, D_TO, ProfitCase


@tagged("post_install", "-at_install")
class TestFloorDirectCosting(ProfitCase):
    def test_price_floor_flags(self):
        # DIF-05: P3 sold below variable cost (15 < 20), P4 below full cost (10 <= 25 < 30), P1 fine
        self._invoice(self.c2, [(self.p3, 4, 15.0, 0.0), (self.p4, 2, 25.0, 0.0)], date(2027, 3, 18), user=self.salesperson_2)
        run = self._run()
        self.assertRecordValues(self._product(run, self.p3) | self._product(run, self.p4) | self._product(run, self.p1), [
            {"avg_price": 15.0, "variable_unit_cost": 20.0, "full_unit_cost": 35.0, "floor_status": "below_variable"},
            {"avg_price": 25.0, "variable_unit_cost": 10.0, "full_unit_cost": 30.0, "floor_status": "below_full"},
            {"avg_price": 95.0, "variable_unit_cost": 40.0, "full_unit_cost": 60.0, "floor_status": "ok"},
        ])
        self.assertEqual(self._fact(run, self.p3, self.c2).floor_status, "below_variable")
        self.assertEqual(self._fact(run, self.p1, self.c1).floor_status, "ok")

    def test_rn001_floor_on_product_unit_price(self):
        # RN-001 / DIF-05: 1 Dozen @90 = 7.5 per unit, below the variable rate 8 -> below_variable (not 90 > 8 ok)
        run = self._packaging_sale()
        self.assertRecordValues(self._product(run, self.p5), [
            {"quantity": 12.0, "avg_price": 7.5, "variable_unit_cost": 8.0, "floor_status": "below_variable"},
        ])
        self.assertEqual(run.fact_ids.filtered(lambda f: f.product_id == self.p5).floor_status, "below_variable")

    def test_direct_costing_by_product(self):
        # DC-02: the margin statement is fed from actual sales and the run costs, by product
        run = self._run()
        dc = run.action_cmc_generate_direct_costing(dimension="product")
        self.assertEqual(dc._name, "cmc.direct.costing")
        self.assertRecordValues(dc, [{"company_id": self.company.id, "date_from": D_FROM, "date_to": D_TO, "mode": "evoluto",
                                      "common_fixed_cost": 1405.0, "operating_result": 395.0}])
        lines = {line.name: line for line in dc.line_ids}
        self.assertEqual(set(lines), {self.p1.display_name, self.p2.display_name, "Unassigned"})
        self.assertRecordValues(lines[self.p1.display_name] | lines[self.p2.display_name] | lines["Unassigned"], [
            {"revenue": 1900.0, "variable_cost": 800.0, "specific_fixed_cost": 200.0},
            {"revenue": 1400.0, "variable_cost": 560.0, "specific_fixed_cost": 140.0},
            {"revenue": 200.0, "variable_cost": 0.0, "specific_fixed_cost": 0.0},
        ])

    def test_direct_costing_by_customer(self):
        # DC-02: by customer the specific fixed costs are product-sustaining + cost-to-serve + channel share
        run = self._run()
        dc = run.action_cmc_generate_direct_costing(dimension="customer")
        lines = {line.name: line for line in dc.line_ids}
        self.assertRecordValues(lines[self.c1.display_name] | lines[self.c2.display_name] | lines[self.c3.display_name], [
            {"revenue": 1800.0, "variable_cost": 760.0, "specific_fixed_cost": 540.0},
            {"revenue": 1000.0, "variable_cost": 400.0, "specific_fixed_cost": 400.0},
            {"revenue": 500.0, "variable_cost": 200.0, "specific_fixed_cost": 600.0},
        ])
        self.assertAlmostEqual(dc.common_fixed_cost, 205.0)
        self.assertAlmostEqual(dc.operating_result, 395.0)

    def test_direct_costing_by_segment(self):
        # DC-02: by segment (attribute snapshot): Key = C1, SME = C2 + C3
        run = self._run()
        dc = run.action_cmc_generate_direct_costing(dimension="segment")
        lines = {line.name: line for line in dc.line_ids}
        self.assertAlmostEqual(lines[self.seg_sme.display_name].revenue, 1500.0)
        self.assertAlmostEqual(lines[self.seg_sme.display_name].specific_fixed_cost, 1000.0)
        self.assertAlmostEqual(dc.operating_result, 395.0)

    def test_direct_costing_needs_computed_run(self):
        # AD-113: a draft run has no figures to feed a statement
        run = self._run(compute=False)
        with self.assertRaises(UserError):
            run.action_cmc_generate_direct_costing(dimension="product")
