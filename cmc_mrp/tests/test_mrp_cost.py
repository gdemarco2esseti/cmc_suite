from odoo.tests.common import TransactionCase


class TestMrpCost(TransactionCase):
    """Fase 1.5c — feed prime cost (MP materials, MOD labor) from MRP."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.AL = cls.env["account.analytic.line"]
        cls.plan = cls.env["account.analytic.plan"].search(
            [("parent_id", "=", False)]
        ).filtered(lambda p: p._column_name() == "account_id")[:1]
        cls.center = cls.env["account.analytic.account"].create(
            {"name": "Reparto MRP", "plan_id": cls.plan.id, "cmc_center_type": "final"}
        )
        cls.mp = cls.env.ref("cmc_base.cmc_element_mp")

    # MOD: native MO labor line (category manufacturing_order) gets enriched
    def test_mod_enrich_manufacturing_line(self):
        line = self.AL.create({
            "name": "[WC] op", "account_id": self.center.id,
            "category": "manufacturing_order", "amount": -50.0, "unit_amount": 2.0,
        })
        self.assertEqual(line.cmc_behavior, "variable")
        self.assertEqual(line.cmc_directness, "direct")
        mod = self.env.ref("cmc_base.cmc_element_mod", raise_if_not_found=False)
        if mod:
            self.assertEqual(line.cmc_cost_element_id, mod)

    # MP: materials consumed on MO done create classified cost lines
    def test_mp_lines_created_on_done(self):
        categ = self.env["product.category"].create({
            "name": "MProd",
            "property_cost_method": "standard",
            "property_valuation": "periodic",
        })
        comp = self.env["product.product"].create({
            "name": "Comp", "is_storable": True,
            "categ_id": categ.id, "standard_price": 7.0,
        })
        fp = self.env["product.product"].create({
            "name": "Fin", "is_storable": True, "categ_id": categ.id,
        })
        bom = self.env["mrp.bom"].create({
            "product_tmpl_id": fp.product_tmpl_id.id,
            "product_qty": 1.0, "type": "normal",
            "bom_line_ids": [(0, 0, {"product_id": comp.id, "product_qty": 3.0})],
        })
        mo = self.env["mrp.production"].create({
            "product_id": fp.id, "product_qty": 1.0, "bom_id": bom.id,
            "cmc_center_id": self.center.id,
        })
        mo.action_confirm()
        mo.qty_producing = 1.0
        for m in mo.move_raw_ids:
            m.quantity = m.product_uom_qty
            m.picked = True
        mo.with_context(skip_consumption=True).button_mark_done()

        lines = mo.move_raw_ids.cmc_mrp_analytic_line_ids
        self.assertTrue(lines, "MP analytic line must be created on MO done")
        self.assertEqual(lines.cmc_cost_element_id, self.mp)
        self.assertEqual(lines.cmc_behavior, "variable")
        self.assertEqual(lines.cmc_directness, "direct")
        # 3 units * 7.0 standard cost = 21 -> stored as negative (cost)
        self.assertAlmostEqual(sum(lines.mapped("amount")), -21.0, places=2)

    # idempotency: re-running creation does not duplicate lines
    def test_mp_no_double_imputation(self):
        categ = self.env["product.category"].create({
            "name": "MProd2",
            "property_cost_method": "standard",
            "property_valuation": "periodic",
        })
        comp = self.env["product.product"].create({
            "name": "Comp2", "is_storable": True,
            "categ_id": categ.id, "standard_price": 5.0,
        })
        fp = self.env["product.product"].create({
            "name": "Fin2", "is_storable": True, "categ_id": categ.id,
        })
        bom = self.env["mrp.bom"].create({
            "product_tmpl_id": fp.product_tmpl_id.id,
            "product_qty": 1.0, "type": "normal",
            "bom_line_ids": [(0, 0, {"product_id": comp.id, "product_qty": 2.0})],
        })
        mo = self.env["mrp.production"].create({
            "product_id": fp.id, "product_qty": 1.0, "bom_id": bom.id,
            "cmc_center_id": self.center.id,
        })
        mo.action_confirm()
        mo.qty_producing = 1.0
        for m in mo.move_raw_ids:
            m.quantity = m.product_uom_qty
            m.picked = True
        mo.with_context(skip_consumption=True).button_mark_done()

        count1 = len(mo.move_raw_ids.cmc_mrp_analytic_line_ids)
        mo._cmc_create_material_lines()  # explicit re-run
        count2 = len(mo.move_raw_ids.cmc_mrp_analytic_line_ids)
        self.assertEqual(count1, count2)
