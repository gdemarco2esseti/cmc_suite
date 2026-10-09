"""KPI-02 non-financial feeders (AD-107): scrap from stock.scrap, shipments / on-time shipments from stock.picking.

Data come from REAL validated transfers and scraps (fixture): Jan 4 deliveries (scheduled 10, 10, 20, 10; done 8,
10 at 09:00, 15, 12 -> 3 on time, the one done on the 12th is late), Feb 5 deliveries all on time, a Jan receipt
(not a delivery). Scrap done: Jan 50, Feb 60 + 40 = 100, Mar 20.
  shipments Jan 4 / Feb 5; on time Jan 3 / Feb 5 -> OTD 75 % / 100 %.
  yield = good / (good + scrap) x 100: Jan 950 / 1,000 = 95 %, Feb 900 / 1,000 = 90 %, Mar 980 / 1,000 = 98 %.
"""

from datetime import date

from odoo.tests import tagged

from .common import FEB, JAN, MAR, KpiCase, dt


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
