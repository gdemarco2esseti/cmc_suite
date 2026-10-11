"""Stress test 2026-10 (rilancio) - the joint cost error names the column to fill, as labelled on screen.

``cmc.joint.run._allocate`` with no positive allocation base raises UserError carrying the method label
("Sales Value at Split-off") and the label of the line column the user must fill ("Unit Sales Value"), never the
technical codes.
"""

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestJointStressRilancio(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.product_a = cls.env["product.product"].create({"name": "JRL A"})
        cls.product_b = cls.env["product.product"].create({"name": "JRL B"})
        cls.product_c = cls.env["product.product"].create({"name": "JRL by-product"})

    def _error(self, method, lines):
        run = self.env["cmc.joint.run"].with_context(lang="en_US").create({
            "name": "JRL joint", "method": method, "joint_cost": 1000, "line_ids": [(0, 0, line) for line in lines],
        })
        with self.assertRaises(UserError) as caught:
            run.action_compute()
        return str(caught.exception)

    def test_sales_value_zero_prices_names_column_and_method(self):
        # FX-006 bis: the user is told WHICH column to fill, with the label of the method, not 'sales_value'
        message = self._error("sales_value", [
            {"product_id": self.product_a.id, "quantity": 60, "unit_sales_value": 0},
            {"product_id": self.product_b.id, "quantity": 40, "unit_sales_value": 0},
        ])
        label = self.env["cmc.joint.line"]._fields["unit_sales_value"].string
        self.assertIn(label, message)
        self.assertIn("Sales Value at Split-off", message)
        self.assertNotIn("sales_value", message)
        self.assertNotIn("unit_sales_value", message)

    def test_sales_value_main_lines_zero_byproduct_priced_still_refused(self):
        # FX-006 bis: a priced by-product does not make a base for the main products; the message stays readable
        message = self._error("sales_value", [
            {"product_id": self.product_a.id, "quantity": 60, "unit_sales_value": 0},
            {"product_id": self.product_b.id, "quantity": 40, "unit_sales_value": 0},
            {"product_id": self.product_c.id, "quantity": 10, "unit_sales_value": 1, "is_byproduct": True},
        ])
        self.assertIn(self.env["cmc.joint.line"]._fields["unit_sales_value"].string, message)
        self.assertNotIn("sales_value", message)
