"""AD-106 - cmc_mrp stamps the cost object on manufacturing cost lines (wave A0).

CONTRACT
- Every MP line the bridge books or adopts for a done MO, and every workcenter (MOD) analytic line of
  its work orders (``mrp.workorder.wc_analytic_account_line_ids``), carries
  ``account.analytic.line.cmc_cost_object_id`` = ``cmc.cost.object`` of type ``product`` for the
  FINISHED product of the MO (``mrp.production.product_id``), in the MO's company.
- ``account.analytic.line.product_id`` keeps the raw material on MP lines (inv. 4: it is NOT the cost
  object). The shop-floor user closing the order needs no CMC right (object created in sudo).
- RN-003 (owner decision): the stamp applies ONLY to MOs with ``cmc_center_id`` (same rule as MP lines
  and the produced-qty feeder): the workcenter line of an MO without center keeps no cost object, and
  that MO feeds no produced quantity.
"""

from unittest.mock import patch

from odoo.tests.common import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestMrpCostObjectStamp(TransactionCase):
    """AD-106: the finished product of the MO is the cost object of its prime cost."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.AL = cls.env["account.analytic.line"]
        cls.AA = cls.env["account.analytic.account"]
        cls.plan = cls.env["account.analytic.plan"].search([("parent_id", "=", False)]).filtered(
            lambda p: p._column_name() == "account_id"
        )[:1]
        cls.center = cls.AA.create({"name": "Reparto CO", "plan_id": cls.plan.id, "cmc_center_type": "final"})
        cls.categ = cls.env["product.category"].create(
            {"name": "CO periodic", "property_cost_method": "standard", "property_valuation": "periodic"}
        )
        cls.workcenter = cls.env["mrp.workcenter"].create(
            {"name": "WC CO", "costs_hour": 30.0, "analytic_distribution": {str(cls.center.id): 100}}
        )

    def _mo(self, name, center=None, company=None, with_operation=False, categ=None):
        env = self.env if not company else self.env["mrp.production"].with_company(company).env
        categ = categ or self.categ
        comp = env["product.product"].create(
            {"name": "Comp %s" % name, "is_storable": True, "categ_id": categ.id, "standard_price": 7.0}
        )
        finished = env["product.product"].create({"name": "Fin %s" % name, "is_storable": True, "categ_id": categ.id})
        bom_vals = {
            "product_tmpl_id": finished.product_tmpl_id.id,
            "product_qty": 1.0,
            "type": "normal",
            "bom_line_ids": [(0, 0, {"product_id": comp.id, "product_qty": 3.0})],
        }
        if with_operation:
            bom_vals["operation_ids"] = [(0, 0, {"name": "Assembly", "workcenter_id": self.workcenter.id, "time_cycle_manual": 60.0})]
        bom = env["mrp.bom"].create(bom_vals)
        vals = {"product_id": finished.id, "product_qty": 1.0, "bom_id": bom.id}
        if center is not False:
            vals["cmc_center_id"] = (center or self.center).id
        if company:
            vals["company_id"] = company.id
        return env["mrp.production"].create(vals)

    def _mark_done(self, mo, user=None, duration=None):
        production = mo.with_user(user) if user else mo
        production.action_confirm()
        if duration is not None:
            production.workorder_ids.duration = duration
        production.qty_producing = 1.0
        for move in production.move_raw_ids:
            move.quantity = move.product_uom_qty
            move.picked = True
        production.with_context(skip_consumption=True).button_mark_done()
        mo.invalidate_recordset()
        return mo

    def test_mp_line_stamped_with_finished_product(self):
        # AD-106: MP cost belongs to the finished product, not to the raw material
        mo = self._mark_done(self._mo("mp"))
        line = mo.move_raw_ids.cmc_mrp_analytic_line_ids
        self.assertEqual(len(line), 1)
        self.assertRecordValues(
            line.cmc_cost_object_id,
            [{"object_type": "product", "res_model": "product.product", "res_id": mo.product_id.id,
              "company_id": mo.company_id.id}],
        )
        # inv.(4): product_id on an MP line stays the raw material
        self.assertEqual(line.product_id, mo.move_raw_ids.product_id)

    def test_adopted_core_line_stamped(self):
        # AD-106: a core line adopted by the bridge is stamped like a created one
        categ = self.env["product.category"].create(
            {"name": "CO RT", "property_cost_method": "standard", "property_valuation": "real_time"}
        )
        mo = self._mo("adopt", categ=categ)
        distribution = {str(self.center.id): 100}
        StockMove = type(self.env["stock.move"])
        with patch.object(StockMove, "_get_analytic_distribution", lambda move: dict(distribution)):
            self._mark_done(mo)
        line = self.AL.search([("account_id", "=", self.center.id), ("cmc_mrp_move_id", "=", mo.move_raw_ids.id)])
        self.assertEqual(len(line), 1)
        self.assertEqual(line.cmc_cost_object_id.res_id, mo.product_id.id)

    def test_mod_workcenter_line_stamped_with_finished_product(self):
        # AD-106: MOD booked on the workcenter belongs to the finished product too
        mo = self._mark_done(self._mo("mod", with_operation=True), duration=60.0)
        lines = mo.workorder_ids.wc_analytic_account_line_ids
        self.assertTrue(lines, "precondition: core books the workcenter time on the center")
        for line in lines:
            self.assertEqual(line.cmc_cost_object_id.res_model, "product.product")
            self.assertEqual(line.cmc_cost_object_id.res_id, mo.product_id.id)

    def test_mod_line_of_mo_without_center_not_stamped(self):
        # RN-003: no MO center, no CMC costing: the workcenter line keeps no cost object, no produced qty
        mo = self._mark_done(self._mo("nocenter", center=False, with_operation=True), duration=60.0)
        self.assertFalse(mo.cmc_center_id, "precondition: MO without CMC center")
        lines = mo.workorder_ids.wc_analytic_account_line_ids
        self.assertTrue(lines, "precondition: core books the workcenter time on the workcenter distribution")
        self.assertFalse(lines.cmc_cost_object_id)
        today = mo.date_finished.date()
        Fact = self.env["cmc.measure.quantity"]
        Fact._cmc_refresh_feeders(today.replace(day=1), today, company=mo.company_id, keys=["mrp_produced_qty"])
        facts = Fact.search([("feeder_key", "=", "mrp_produced_qty"), ("cost_object_id.res_id", "=", mo.product_id.id),
                             ("cost_object_id.res_model", "=", "product.product")])
        self.assertFalse(facts)

    def test_two_mos_same_product_share_one_object(self):
        # AD-106 inv.(1): groupable cost per product
        mo1 = self._mark_done(self._mo("share"))
        mo2 = self.env["mrp.production"].create(
            {"product_id": mo1.product_id.id, "product_qty": 1.0, "bom_id": mo1.bom_id.id, "cmc_center_id": self.center.id}
        )
        self._mark_done(mo2)
        self.assertEqual(
            mo1.move_raw_ids.cmc_mrp_analytic_line_ids.cmc_cost_object_id,
            mo2.move_raw_ids.cmc_mrp_analytic_line_ids.cmc_cost_object_id,
        )

    def test_object_in_mo_company(self):
        # AD-115: the object of a company-B order lives in company B
        company_b = self.env["res.company"].create({"name": "MRP CO Company B"})
        self.env.user.company_ids = [(4, company_b.id)]
        center_b = self.AA.create(
            {"name": "Reparto CO B", "plan_id": self.plan.id, "cmc_center_type": "final", "company_id": company_b.id}
        )
        mo = self._mark_done(self._mo("mc", center=center_b, company=company_b))
        line = mo.move_raw_ids.cmc_mrp_analytic_line_ids
        self.assertEqual(line.cmc_cost_object_id.company_id, company_b)

    def test_shop_floor_user_gets_lines_stamped(self):
        # AD-106: objects are created by the system; the operator has no CMC right
        user = new_test_user(self.env, login="cmc_co_shopfloor", groups="base.group_user,mrp.group_mrp_user")
        mo = self._mark_done(self._mo("acl"), user=user)
        line = mo.move_raw_ids.cmc_mrp_analytic_line_ids
        self.assertEqual(line.cmc_cost_object_id.res_id, mo.product_id.id)
