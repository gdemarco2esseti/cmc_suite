"""AD-107 / AD-111 - cmc_mrp declares the produced-quantity feeder (wave A0).

CONTRACT
- Data: driver ``cmc_mrp.cmc_measure_produced_qty`` (``cmc.cost.driver``, ``company_id`` False,
  ``source`` = ``feeder``, ``feeder_key`` = ``mrp_produced_qty``).
- ``cmc.measure.quantity._cmc_feeder_profiles()`` includes a profile with key ``mrp_produced_qty``.
- After ``_cmc_refresh_feeders(date_from, date_to, company=..., keys=["mrp_produced_qty"])`` there is one
  actual fact per (finished product, MO cost center, month, company): ``quantity`` = finished quantity of
  DONE finished moves of MOs, NET of unbuild orders (AD-111 reversibility); ``cost_object_id`` = product
  object of the finished product; ``center_id`` = ``mrp.production.cmc_center_id``;
  ``feeder_key`` = ``mrp_produced_qty``; ``version_id`` empty. Idempotent.
- RN-001: the quantity is the ACTUALLY produced one (``stock.move.quantity`` of the done finished move,
  converted to the product UoM), not the demand: an MO for 10 closed with 12 produced feeds 12; an MO in
  Dozens feeds Units of the product.
"""

import calendar
from datetime import date

from odoo import fields
from odoo.tests.common import TransactionCase, tagged

KEY = "mrp_produced_qty"


def month_start(day):
    return day.replace(day=1)


def month_end(day):
    return day.replace(day=calendar.monthrange(day.year, day.month)[1])


@tagged("post_install", "-at_install")
class TestProducedFeeder(TransactionCase):
    """AD-107: produced quantities flow from MRP into the quantity fact table."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Fact = cls.env["cmc.measure.quantity"]
        plan = cls.env["account.analytic.plan"].search([("parent_id", "=", False)]).filtered(
            lambda p: p._column_name() == "account_id"
        )[:1]
        cls.plan = plan
        cls.center = cls.env["account.analytic.account"].create(
            {"name": "Reparto feed", "plan_id": plan.id, "cmc_center_type": "final"}
        )
        cls.categ = cls.env["product.category"].create(
            {"name": "FEED", "property_cost_method": "standard", "property_valuation": "periodic"}
        )
        cls.finished = cls.env["product.product"].create({"name": "Fin feed", "is_storable": True, "categ_id": cls.categ.id})
        comp = cls.env["product.product"].create(
            {"name": "Comp feed", "is_storable": True, "categ_id": cls.categ.id, "standard_price": 1.0}
        )
        cls.bom = cls.env["mrp.bom"].create({
            "product_tmpl_id": cls.finished.product_tmpl_id.id, "product_qty": 1.0, "type": "normal",
            "bom_line_ids": [(0, 0, {"product_id": comp.id, "product_qty": 1.0})],
        })
        cls.today = fields.Date.context_today(cls.env.user)
        cls.span = (month_start(cls.today), month_end(cls.today))

    def _produce(self, qty, center=None, company=None, bom=None, product=None, produced=None, uom=None):
        env = self.env if not company else self.env["mrp.production"].with_company(company).env
        vals = {"product_id": (product or self.finished).id, "product_qty": qty, "bom_id": (bom or self.bom).id,
                "cmc_center_id": (center or self.center).id}
        if company:
            vals["company_id"] = company.id
        if uom:
            vals["product_uom_id"] = uom.id
        mo = env["mrp.production"].create(vals)
        mo.action_confirm()
        mo.qty_producing = qty if produced is None else produced
        for move in mo.move_raw_ids:
            move.quantity = move.product_uom_qty
            move.picked = True
        mo.with_context(skip_consumption=True).button_mark_done()
        return mo

    def _refresh(self, company=None):
        self.Fact._cmc_refresh_feeders(*self.span, company=company or self.env.company, keys=[KEY])

    def _facts(self, company=None):
        return self.Fact.search([("feeder_key", "=", KEY), ("company_id", "=", (company or self.env.company).id)])

    def test_profile_and_driver_declared(self):
        # AD-107: the bridge declares its feeder; the measure ships with it
        driver = self.env.ref("cmc_mrp.cmc_measure_produced_qty")
        self.assertRecordValues(driver, [{"source": "feeder", "feeder_key": KEY, "company_id": False}])
        self.assertIn(KEY, [profile["key"] for profile in self.Fact._cmc_feeder_profiles()])

    def test_produced_quantity_per_product_and_month(self):
        # AD-107/FC-05: produced qty per finished product x center x month
        self._produce(2.0)
        self._produce(3.0)
        self._refresh()
        fact = self._facts()
        self.assertEqual(len(fact), 1)
        self.assertRecordValues(
            fact,
            [{"driver_id": self.env.ref("cmc_mrp.cmc_measure_produced_qty").id, "date": month_start(self.today),
              "quantity": 5.0, "center_id": self.center.id, "version_id": False}],
        )
        self.assertEqual(fact.cost_object_id.res_model, "product.product")
        self.assertEqual(fact.cost_object_id.res_id, self.finished.id)

    def test_refresh_is_idempotent(self):
        # AD-107: replace, never append
        self._produce(2.0)
        self._refresh()
        self._refresh()
        self.assertEqual(self._facts().mapped("quantity"), [2.0])

    def test_unbuild_is_netted(self):
        # AD-111 MRP reversibility: an unbuild reverses a produced quantity
        mo = self._produce(3.0)
        self._refresh()
        unbuild = self.env["mrp.unbuild"].create(
            {"mo_id": mo.id, "product_id": self.finished.id, "bom_id": self.bom.id, "product_qty": 1.0}
        )
        unbuild.action_validate()
        self._refresh()
        self.assertEqual(self._facts().mapped("quantity"), [2.0])

    def test_unfinished_orders_not_counted(self):
        # AD-111: only done moves are produced quantities
        mo = self.env["mrp.production"].create(
            {"product_id": self.finished.id, "product_qty": 4.0, "bom_id": self.bom.id, "cmc_center_id": self.center.id}
        )
        mo.action_confirm()
        self._refresh()
        self.assertFalse(self._facts())

    def test_company_isolation(self):
        # AD-115: a company-B order feeds company-B facts only
        company_b = self.env["res.company"].create({"name": "Feed Company B"})
        self.env.user.company_ids = [(4, company_b.id)]
        center_b = self.env["account.analytic.account"].create(
            {"name": "Reparto feed B", "plan_id": self.plan.id, "cmc_center_type": "final", "company_id": company_b.id}
        )
        self._produce(1.0)
        # the BoM must belong to the MO company (core _check_company on confirm)
        bom_b = self.bom.with_company(company_b).copy({"company_id": company_b.id})
        self._produce(7.0, center=center_b, company=company_b, bom=bom_b)
        self._refresh()
        self._refresh(company=company_b)
        self.assertEqual(self._facts().mapped("quantity"), [1.0])
        self.assertEqual(self._facts(company_b).mapped("quantity"), [7.0])
        self.assertEqual(self._facts(company_b).cost_object_id.company_id, company_b)

    def test_months_outside_span_untouched(self):
        # AD-107: a refresh of this month does not rewrite another month
        old = self.Fact.create({
            "driver_id": self.env.ref("cmc_mrp.cmc_measure_produced_qty").id, "date": date(2020, 1, 1),
            "quantity": 9.0, "feeder_key": KEY,
        })
        self._produce(1.0)
        self._refresh()
        self.assertTrue(old.exists())

    def test_overproduction_feeds_actual_quantity(self):
        # RN-001 / AD-107: the measure is what was produced (12), not what was planned (10)
        mo = self._produce(10.0, produced=12.0)
        finished_move = mo.move_finished_ids.filtered(lambda move: move.product_id == self.finished)
        self.assertEqual(finished_move.state, "done", "precondition: the finished move is done")
        self.assertEqual(finished_move.quantity, 12.0, "precondition: core records 12 produced")
        self._refresh()
        self.assertEqual(self._facts().mapped("quantity"), [12.0])

    def test_mo_uom_converted_to_product_uom(self):
        # RN-001 / AD-107: an MO in Dozens feeds the product UoM (Units): 1 dozen -> 12
        dozen = self.env.ref("uom.product_uom_dozen")
        self.assertNotEqual(self.finished.uom_id, dozen, "precondition: product is not in dozens")
        mo = self._produce(1.0, uom=dozen)
        finished_move = mo.move_finished_ids.filtered(lambda move: move.product_id == self.finished)
        self.assertEqual(finished_move.product_uom, dozen, "precondition: finished move in dozens")
        self._refresh()
        self.assertEqual(self._facts().mapped("quantity"), [12.0])

    def test_overproduction_in_other_uom(self):
        # RN-001: actual quantity AND UoM conversion together: planned 1 dozen, produced 2 dozens -> 24 units
        self._produce(1.0, produced=2.0, uom=self.env.ref("uom.product_uom_dozen"))
        self._refresh()
        self.assertEqual(self._facts().mapped("quantity"), [24.0])
