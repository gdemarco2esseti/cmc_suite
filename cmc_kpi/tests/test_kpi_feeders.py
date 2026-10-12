"""KPI-02 non-financial feeders (AD-107): scrap from stock.scrap, shipments / on-time shipments from stock.picking.

Data come from REAL validated transfers and scraps (fixture): Jan 4 deliveries (scheduled 10, 10, 20, 10; done 8,
10 at 09:00, 15, 12 -> 3 on time, the one done on the 12th is late), Feb 5 deliveries all on time, a Jan receipt
(not a delivery). Scrap done: Jan 50, Feb 60 + 40 = 100, Mar 20.
  shipments Jan 4 / Feb 5; on time Jan 3 / Feb 5 -> OTD 75 % / 100 %.
  yield = good / (good + scrap) x 100: Jan 950 / 1,000 = 95 %, Feb 900 / 1,000 = 90 %, Mar 980 / 1,000 = 98 %.
RN-001 (April 2026, real flow, nothing rewritten after validation): on time = day of the transfer <= day of the
deadline (else of the scheduled date), in the company timezone; returns are not shipments.
"""

from datetime import date, datetime

from freezegun import freeze_time

from odoo import Command
from odoo.tests import tagged

from .common import FEB, JAN, MAR, KpiCase, dt

APR = date(2026, 4, 1)


def apr(day, hour, minute=0, second=0):
    return datetime(2026, 4, day, hour, minute, second)


@tagged("post_install", "-at_install")
class TestKpiFeeders(KpiCase):
    def _facts(self, xmlid):
        driver = self.env.ref(xmlid)
        facts = self.env["cmc.measure.quantity"].search(
            [("driver_id", "=", driver.id), ("company_id", "=", self.company.id), ("version_id", "=", False)]
        )
        totals = {}
        for fact in facts:
            totals[fact.date] = totals.get(fact.date, 0.0) + fact.quantity
        return totals

    def test_shipped_measures(self):
        # KPI-02: the three feeder measures ship as statistical, feeder-sourced, shared measures
        for xmlid, key in (("cmc_kpi.cmc_measure_scrap_qty", "stock_scrap_qty"),
                           ("cmc_kpi.cmc_measure_shipments", "stock_otd_shipped"),
                           ("cmc_kpi.cmc_measure_shipments_on_time", "stock_otd_on_time")):
            self.assertRecordValues(self.env.ref(xmlid), [{"measure_kind": "statistical", "source": "feeder",
                                                           "feeder_key": key, "company_id": False}])

    def test_on_time_flag_on_pickings(self):
        # KPI-02: on time = done no later than scheduled (stored, so the feeder groups on it)
        self.assertEqual(self.deliveries.mapped("cmc_on_time"), [True, True, True, False] + [True] * 5)
        self.assertTrue(self.env["stock.picking"]._fields["cmc_on_time"].store)

    def test_shipments_feeder(self):
        # KPI-02: count of done outgoing transfers per month; the receipt is not a shipment
        self.assertEqual(self._facts("cmc_kpi.cmc_measure_shipments"), {JAN: 4.0, FEB: 5.0})

    def test_on_time_feeder(self):
        # KPI-02: count of the on-time ones
        self.assertEqual(self._facts("cmc_kpi.cmc_measure_shipments_on_time"), {JAN: 3.0, FEB: 5.0})

    def test_scrap_feeder(self):
        # KPI-02: scrapped quantity per month (done scraps)
        self.assertEqual(self._facts("cmc_kpi.cmc_measure_scrap_qty"), {JAN: 50.0, FEB: 100.0, MAR: 20.0})

    def test_scrap_fact_per_product_cost_object(self):
        # AD-106: the scrap fact is keyed by the product cost object
        facts = self.env["cmc.measure.quantity"].search([("driver_id", "=", self.env.ref("cmc_kpi.cmc_measure_scrap_qty").id),
                                                         ("date", "=", JAN)])
        self.assertEqual(facts.cost_object_id.res_id, self.product.id)

    def test_refresh_is_idempotent(self):
        # AD-107: a second refresh replaces the month, never appends
        self.env["cmc.measure.quantity"]._cmc_refresh_feeders(JAN, date(2026, 3, 31), company=self.company)
        self.assertEqual(self._facts("cmc_kpi.cmc_measure_shipments"), {JAN: 4.0, FEB: 5.0})

    def test_late_delivery_after_refresh(self):
        # KPI-02: a new late delivery lowers the on-time share at the next refresh
        self._ship([(dt(2, 10), dt(2, 25))])
        self.env["cmc.measure.quantity"]._cmc_refresh_feeders(FEB, date(2026, 2, 28), company=self.company)
        self.assertEqual(self._facts("cmc_kpi.cmc_measure_shipments")[FEB], 6.0)
        self.assertEqual(self._facts("cmc_kpi.cmc_measure_shipments_on_time")[FEB], 5.0)

    def test_yield_from_feeder_scrap(self):
        # KPI-02: yield = good / (good + scrap) with the scrap read from the real feeder
        lib = self._library()
        run = self._run(JAN, MAR)
        for month, expected in ((JAN, 95.0), (FEB, 90.0), (MAR, 98.0)):
            self.assertAlmostEqual(self._val(run, lib["yield_pct"], month).value, expected, places=4)

    def test_otd_from_feeder(self):
        # KPI-02: OTD % from the two shipment measures
        lib = self._library()
        run = self._run(JAN, FEB)
        self.assertAlmostEqual(self._val(run, lib["otd"], JAN).value, 75.0, places=4)
        self.assertAlmostEqual(self._val(run, lib["otd"], FEB).value, 100.0, places=4)

    # ---- RN-001: on time by day, deadline first, company timezone, returns excluded ----

    def _refresh_april(self):
        self.env["cmc.measure.quantity"]._cmc_refresh_feeders(APR, date(2026, 4, 30), company=self.company)
        return self._facts("cmc_kpi.cmc_measure_shipments").get(APR, 0.0), self._facts("cmc_kpi.cmc_measure_shipments_on_time").get(APR, 0.0)

    def test_default_scheduled_validated_seconds_later_is_on_time(self):
        # RN-001: a transfer created with the default scheduled date (now) and validated 30 s later is on time
        with freeze_time(apr(15, 10)):
            out = self.warehouse.out_type_id
            src, dest = out.default_location_src_id, self.env.ref("stock.stock_location_customers")
            picking = self.env["stock.picking"].create({
                "picking_type_id": out.id, "partner_id": self.customer.id, "location_id": src.id, "location_dest_id": dest.id,
                "move_ids": [Command.create({"product_id": self.product.id, "product_uom": self.product.uom_id.id, "product_uom_qty": 1.0,
                                             "location_id": src.id, "location_dest_id": dest.id})],
            })
            picking.action_confirm()
        self._validate(picking, at=apr(15, 10, 0, 30))
        self.assertGreater(picking.date_done, picking.scheduled_date, "fixture must reveal a second-level comparison")
        self.assertTrue(picking.cmc_on_time)
        self.assertEqual(self._refresh_april(), (1.0, 1.0))

    def test_deadline_tomorrow_done_today_is_on_time(self):
        # RN-001: the deadline wins over an earlier scheduled date: done before the deadline day = on time
        picking = self._ship([(apr(14, 10), apr(15, 15), apr(16, 10))])
        self.assertEqual(picking.date_deadline, apr(16, 10))
        self.assertTrue(picking.cmc_on_time)

    def test_done_after_deadline_day_is_late(self):
        # RN-001: the deadline wins over a later scheduled date: done the day after the deadline = late
        picking = self._ship([(apr(17, 10), apr(15, 9), apr(14, 10))])
        self.assertEqual(picking.date_deadline, apr(14, 10))
        self.assertFalse(picking.cmc_on_time)
        self.assertEqual(self._refresh_april(), (1.0, 0.0))

    def test_same_day_later_hour_is_on_time(self):
        # RN-001: the promise is a day, not an hour: scheduled 10:00, done 18:00 the same day = on time
        self.assertTrue(self._ship([(apr(20, 10), apr(20, 18))]).cmc_on_time)

    def test_on_time_day_in_company_timezone(self):
        # RN-001: the day is read in the company timezone (Rome = UTC+2 in April), not in UTC
        self.company.partner_id.tz = "Europe/Rome"
        # promised 23:30 Rome of the 15th (21:30 UTC), done 00:30 Rome of the 16th (22:30 UTC): late in Rome
        late = self._ship([(apr(15, 21, 30), apr(15, 22, 30))])
        # promised 01:30 Rome of the 16th (23:30 UTC of the 15th), done 02:30 Rome of the 16th: on time in Rome
        on_time = self._ship([(apr(15, 23, 30), apr(16, 0, 30))])
        self.assertEqual((late.cmc_on_time, on_time.cmc_on_time), (False, True))

    def test_return_to_supplier_is_not_a_shipment(self):
        # RN-001: a return to supplier is an outgoing transfer but no customer delivery: not counted
        receipt = self._ship([(apr(10, 9), apr(10, 10))], picking_type=self.warehouse.in_type_id)
        wizard = self.env["stock.return.picking"].with_context(active_id=receipt.id, active_model="stock.picking").create({})
        wizard.product_return_moves.quantity = 1.0
        back = self.env["stock.picking"].browse(wizard.action_create_returns()["res_id"])
        self._validate(back, at=apr(12, 10))
        self.assertRecordValues(back, [{"state": "done", "picking_type_code": "outgoing", "return_id": receipt.id}])
        self.assertEqual(self._refresh_april(), (0.0, 0.0))
