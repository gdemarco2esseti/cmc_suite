"""Stress test 2026-10 (corsia A) - FX-006 (a): the joint cost error names the method with its label."""

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestFx006JointMethodLabel(TransactionCase):
    """FX-006 (a) / RF-UX-010: messages use the labels the user sees, never internal codes."""

    def test_fx006a_sales_value_zero_prices_message_uses_label(self):
        # FX-006 (a): sales value method with zero prices -> the error shows "Sales Value at Split-off", not 'sales_value'
        product_a = self.env["product.product"].create({"name": "FX006 A"})
        product_b = self.env["product.product"].create({"name": "FX006 B"})
        run = self.env["cmc.joint.run"].create({
            "name": "FX006 joint",
            "method": "sales_value",
            "joint_cost": 1000,
            "line_ids": [
                (0, 0, {"product_id": product_a.id, "quantity": 60, "unit_sales_value": 0}),
                (0, 0, {"product_id": product_b.id, "quantity": 40, "unit_sales_value": 0}),
            ],
        })
        with self.assertRaises(UserError) as caught:
            run.action_compute()
        message = str(caught.exception)
        label = dict(run.fields_get(["method"])["method"]["selection"])["sales_value"]
        self.assertIn(label, message)
        self.assertNotIn("sales_value", message)
