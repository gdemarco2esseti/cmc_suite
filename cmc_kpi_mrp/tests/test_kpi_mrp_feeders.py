"""KPI-02 residual: the MRP time / rework feeders (AD-107) on real done MOs and work orders (numbers in common.py).

Facts per (month, center) of the fixture:
  wo minutes      Jan press 472 + 336 = 808, rework 192; Feb press 420
  value added     Jan press 444 + 336 = 780, rework 192; Feb press 420   (MO1 Assemble: 28 min performance loss)
  lead minutes    Jan press 4,320 + 1,440 = 5,760, rework 720; Feb press 2,100
  MOs done        Jan press 2, rework 1; Feb press 1
  rework qty      Jan rework 5
"""

from datetime import date

from freezegun import freeze_time

from odoo import Command
from odoo.tests import tagged

from .common import FEB, JAN, MAR, MEASURES, MRP_KEYS, KpiMrpCase, dt


@tagged("post_install", "-at_install")
class TestKpiMrpFeeders(KpiMrpCase):
    def test_fixture_preconditions(self):
        # guard: the core flow produced what the hand numbers assume (else the scenario proves nothing)
        mo1 = self.mo1
        self.assertRecordValues(mo1, [{"state": "done", "date_finished": dt(1, 8)}])
        # why cmc_date_released: core rewrote the MO start to the close time (minutes logged without Start)
        self.assertEqual(mo1.date_start, mo1.date_finished, "core 19 behaviour the contract relies on changed")
        self.assertEqual(mo1.workorder_ids.sorted(lambda wo: wo.operation_id.id).mapped("duration_expected"), [360.0, 144.0])
        self.assertEqual(sum(mo1.workorder_ids.mapped("duration")), 472.0)
        times = mo1.workorder_ids.time_ids
        self.assertEqual(sum(times.filtered(lambda t: t.loss_type == "productive").mapped("duration")), 444.0)
        self.assertEqual(sum(times.filtered(lambda t: t.loss_type == "performance").mapped("duration")), 28.0)
        self.assertEqual(self.mo_open.state, "progress", "the open MO has logged time but is not done")

    def test_shipped_measures(self):
        # KPI-02 / AD-107: the six measures ship as statistical, feeder-sourced, shared measures
        for xmlid, key in MEASURES.items():
            self.assertRecordValues(self.env.ref(xmlid), [{"measure_kind": "statistical", "source": "feeder",
                                                           "feeder_key": key, "company_id": False}])

    def test_profiles_declared(self):
        # AD-107: the bridge declares its feeders through the profile hook, keeping the others (super)
        keys = {profile["key"] for profile in self.Fact._cmc_feeder_profiles()}
        self.assertLessEqual(set(MRP_KEYS) | {"stock_delivery_lead_minutes", "mrp_produced_qty", "stock_scrap_qty"}, keys)

    def test_release_date_stamped_at_confirmation(self):
        # contract: the release to the shop floor is stamped by the confirmation and survives the close
        self.assertEqual((self.mo1 | self.mo2 | self.mo3 | self.mo4).mapped("cmc_date_released"),
                         [dt(1, 5), dt(1, 12), dt(1, 26), dt(2, 2)])
        self.assertFalse(self.env["mrp.production"]._fields["cmc_date_released"].copy)

    def test_lead_time_field_stored_in_minutes(self):
        # KPI-02 lead time: date_finished - release of a done MO, stored (the feeder groups on it)
        self.assertTrue(self.env["mrp.production"]._fields["cmc_lead_time"].store)
        self.assertEqual((self.mo1 | self.mo2 | self.mo3 | self.mo4).mapped("cmc_lead_time"), [4320.0, 1440.0, 720.0, 2100.0])
        self.assertEqual(self.mo_open.cmc_lead_time, 0.0)

    def test_wo_minutes_per_center_and_month(self):
        # KPI-02 cycle time: all logged work-order minutes of the done MOs, per CMC center and month of close
        self._refresh()
        self.assertEqual(self._facts("mrp_wo_minutes"), {
            (JAN, self.press): 808.0, (JAN, self.rework_center): 192.0, (FEB, self.press): 420.0,
        })

    def test_wo_minutes_keyed_by_product_cost_object(self):
        # AD-106: the time facts are keyed by the cost object of the MO product
        self._refresh()
        facts = self.Fact.search([("feeder_key", "in", MRP_KEYS), ("company_id", "=", self.company.id)])
        self.assertTrue(facts)
        self.assertEqual(set(facts.mapped("cost_object_id.res_model")), {"product.product"})
        self.assertEqual(set(facts.mapped("cost_object_id.res_id")), {self.gear.id})
        expected = {self.env.ref(xmlid).id for xmlid, key in MEASURES.items() if key in MRP_KEYS}
        self.assertEqual(set(facts.mapped("driver_id").ids), expected)

    def test_value_added_is_productive_time_only(self):
        # KPI-02 MCE numerator: productive time; the 28 min of performance loss of MO1 are not value added
        self._refresh()
        self.assertEqual(self._facts("mrp_value_added_minutes"), {
            (JAN, self.press): 780.0, (JAN, self.rework_center): 192.0, (FEB, self.press): 420.0,
        })

    def test_lead_time_and_done_count(self):
        # KPI-02 lead time: summed minutes and number of done MOs (the average is a KPI formula)
        self._refresh()
        self.assertEqual(self._facts("mrp_mo_lead_minutes"), {
            (JAN, self.press): 5760.0, (JAN, self.rework_center): 720.0, (FEB, self.press): 2100.0,
        })
        self.assertEqual(self._facts("mrp_mo_done"), {
            (JAN, self.press): 2.0, (JAN, self.rework_center): 1.0, (FEB, self.press): 1.0,
        })

    def test_rework_quantity(self):
        # KPI-02 FPY: produced quantity of the MOs flagged as rework
        self._refresh()
        self.assertEqual(self._facts("mrp_rework_qty"), {(JAN, self.rework_center): 5.0})

    def test_rework_flag_not_copied(self):
        # a duplicated rework MO is a new order: the marker is chosen again, never inherited
        field = self.env["mrp.production"]._fields["cmc_is_rework"]
        self.assertEqual((field.type, field.copy), ("boolean", False))
        self.assertFalse(self.mo3.copy().cmc_is_rework)

    def test_rework_quantity_in_product_uom(self):
        # AD-107: a rework MO in Dozens feeds Units of the product (1 dozen -> 12)
        self._done_mo(1.0, dt(3, 2), dt(3, 3), center=self.rework_center, rework=True, bom=self.bom_plain,
                      uom=self.env.ref("uom.product_uom_dozen"))
        self._refresh()
        self.assertEqual(self._facts("mrp_rework_qty")[(MAR, self.rework_center)], 12.0)

    def test_unbuild_nets_quantities_not_time(self):
        # AD-111: an unbuild of 1 reworked unit returns a unit (rework 4, produced 24), not the minutes spent
        with freeze_time(dt(1, 28)):
            unbuild = self.env["mrp.unbuild"].create(
                {"mo_id": self.mo3.id, "product_id": self.gear.id, "bom_id": self.bom.id, "product_qty": 1.0}
            )
            unbuild.action_validate()
        self._refresh()
        self.assertEqual(self._facts("mrp_rework_qty"), {(JAN, self.rework_center): 4.0})
        self.assertEqual(self._by_month("mrp_produced_qty")[JAN], 24.0)
        self.assertEqual(self._by_month("mrp_wo_minutes")[JAN], 1000.0)
        self.assertEqual(self._by_month("mrp_mo_lead_minutes")[JAN], 6480.0)
        self.assertEqual(self._by_month("mrp_mo_done")[JAN], 3.0)

    def test_minutes_dated_on_the_month_of_close(self):
        # contract: time logged on Jan 30 of an MO closed on Feb 2 is a February fact (same month as its output)
        mo = self.env["mrp.production"].create({"product_id": self.gear.id, "product_qty": 1.0, "bom_id": self.bom.id,
                                                "cmc_center_id": self.press.id, "date_start": dt(1, 30)})
        with freeze_time(dt(1, 30)):
            mo.action_confirm()
        with freeze_time(dt(1, 30, 18)):
            for workorder, minutes in zip(mo.workorder_ids.sorted(lambda wo: wo.operation_id.id), (30.0, 12.0)):
                workorder.duration = minutes
        with freeze_time(dt(2, 2, 9)):
            self._close(mo, 1.0)
        self._refresh()
        facts = self._facts("mrp_wo_minutes")
        self.assertEqual((facts[(JAN, self.press)], facts[(FEB, self.press)]), (808.0, 462.0))
        self.assertEqual(self._facts("mrp_value_added_minutes")[(FEB, self.press)], 462.0)

    def test_lead_time_starts_at_release_not_at_planned_or_operator_start(self):
        # KPI-02: planned Mar 1, released Mar 2 08:00, started by the operator Mar 3 08:00, closed Mar 3 20:00
        # -> 36 h = 2,160 min (not 3,600 from the plan, not 720 from the start press)
        mo = self.env["mrp.production"].create({"product_id": self.gear.id, "product_qty": 1.0, "bom_id": self.bom.id,
                                                "cmc_center_id": self.press.id, "date_start": dt(3, 1)})
        with freeze_time(dt(3, 2)):
            mo.action_confirm()
        with freeze_time(dt(3, 3)):
            mo.workorder_ids.sorted(lambda wo: wo.operation_id.id)[:1].button_start()
        self.assertEqual(mo.date_start, dt(3, 3), "precondition: core moves the MO start to the start press")
        with freeze_time(dt(3, 3, 20)):
            self._close(mo, 1.0)
        self.assertEqual(mo.cmc_lead_time, 2160.0)
        self._refresh()
        self.assertEqual(self._facts("mrp_mo_lead_minutes")[(MAR, self.press)], 2160.0)

    def test_without_release_stamp_the_creation_starts_the_clock(self):
        # contract: an MO released before the bridge existed (no stamp) is measured from its creation
        mo = self._done_mo(2.0, dt(3, 9), dt(3, 10), bom=self.bom_plain)
        # why SQL: core stamps create_date with the transaction time and refuses it in create/write
        self.env.cr.execute("UPDATE mrp_production SET create_date = %s WHERE id = %s", (dt(3, 8), mo.id))
        mo.invalidate_recordset(["create_date"])
        mo.cmc_date_released = False
        self.assertEqual(mo.cmc_lead_time, 2880.0)

    def test_open_and_centerless_orders_feed_nothing(self):
        # contract: only done MOs inside CMC product costing; no fact without center for the MRP keys
        self._refresh()
        facts = self.Fact.search([("feeder_key", "in", MRP_KEYS), ("company_id", "=", self.company.id)])
        self.assertFalse(facts.filtered(lambda fact: not fact.center_id))
        self.assertEqual(self._by_month("mrp_mo_done"), {JAN: 3.0, FEB: 1.0})

    def test_refresh_is_idempotent(self):
        # AD-107: a second refresh replaces the months, never appends
        self._refresh()
        self._refresh()
        self.assertEqual(self._by_month("mrp_wo_minutes"), {JAN: 1000.0, FEB: 420.0})
        self.assertEqual(self._by_month("mrp_mo_done"), {JAN: 3.0, FEB: 1.0})

    def test_company_isolation(self):
        # AD-115: a company-B MO feeds company-B facts only, and company A's refresh does not see it
        company_b = self.env["res.company"].create({"name": "KPIMRP Company B"})
        self.env.user.company_ids = [Command.link(company_b.id)]
        center_b = self._center("KPIMRP press B", company=company_b)
        bom_b = self.bom_plain.with_company(company_b).copy({"company_id": company_b.id})
        self._done_mo(7.0, dt(1, 6), dt(1, 7), center=center_b, bom=bom_b, company=company_b)
        self._refresh()
        self._refresh(company=company_b)
        self.assertEqual(self._by_month("mrp_mo_done"), {JAN: 3.0, FEB: 1.0})
        self.assertEqual(self._facts("mrp_mo_done", company_b), {(JAN, center_b): 1.0})
        self.assertEqual(self._facts("mrp_mo_lead_minutes", company_b), {(JAN, center_b): 1440.0})

    def test_months_outside_span_untouched(self):
        # AD-107: refreshing February does not rewrite January
        self._refresh()
        january = self.Fact.search([("feeder_key", "=", "mrp_wo_minutes"), ("date", "=", JAN)])
        self._refresh(FEB, date(2026, 2, 28))
        self.assertTrue(january.exists())
