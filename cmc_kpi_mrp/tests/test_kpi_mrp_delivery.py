"""KPI-02 residual: delivery lead time (order or transfer creation -> done) from real validated transfers.

D1 2,880 + D2 720 in Jan (2 shipments) -> 3,600 / 2 / 1,440 = 1.25 days; D3 (created Jan 30, done Feb 2) 4,320 in
Feb -> 3.0 days. The receipt and a return to supplier are not shipments (cmc_kpi RN-001 domain).
Without ``sale_stock`` (this database) the start is the transfer ``create_date``; with it, the sale order date.
"""

from datetime import date

from odoo.tests import tagged

from .common import DELIVERY_KEY, FEB, JAN, KpiDeliveryCase, dt

TOLERANCE = 2


@tagged("post_install", "-at_install")
class TestKpiMrpDelivery(KpiDeliveryCase):
    def test_delivery_lead_time_field(self):
        # KPI-02: minutes from the creation of the transfer to its validation, stored
        self.assertTrue(self.env["stock.picking"]._fields["cmc_delivery_lead_time"].store)
        self.assertEqual((self.d1 | self.d2 | self.d3).mapped("cmc_delivery_lead_time"), [2880.0, 720.0, 4320.0])

    def test_open_transfer_has_no_lead_time(self):
        # contract: 0 while not done
        self.assertEqual(self._ship(dt(2, 10)).cmc_delivery_lead_time, 0.0)

    def test_feeder_by_month_of_delivery(self):
        # KPI-02: summed per month of date_done; the receipt is ignored
        self._refresh(keys=[DELIVERY_KEY])
        self.assertEqual(self._by_month(DELIVERY_KEY), {JAN: 3600.0, FEB: 4320.0})

    def test_return_to_supplier_is_not_a_shipment(self):
        # cmc_kpi RN-001: a return is an outgoing transfer but not a delivery
        wizard = self.env["stock.return.picking"].with_context(active_id=self.receipt.id, active_model="stock.picking").create({})
        wizard.product_return_moves.quantity = 1.0
        back = self.env["stock.picking"].browse(wizard.action_create_returns()["res_id"])
        self._validate(back, dt(2, 20))
        self.assertRecordValues(back, [{"state": "done", "picking_type_code": "outgoing", "return_id": self.receipt.id}])
        self._refresh(keys=[DELIVERY_KEY])
        self.assertEqual(self._by_month(DELIVERY_KEY), {JAN: 3600.0, FEB: 4320.0})

    def test_delivery_lead_time_kpi(self):
        # KPI-02: average delivery lead time in days = lead minutes / shipments / 1,440
        self._refresh()
        lib = self._library()
        run = self._run(kpis=lib["delivery_lead_time"])
        self.assertAlmostEqual(self._val(run, "delivery_lead_time", JAN).value, 1.25, places=4)
        self.assertAlmostEqual(self._val(run, "delivery_lead_time", FEB).value, 3.0, places=4)

    def test_sale_order_date_is_the_start(self):
        # contract: with sale_stock the clock starts at the order (date_order), not at the transfer creation
        if "sale_id" not in self.env["stock.picking"]._fields:
            self.skipTest("sale_stock not installed in this database: branch NOT covered here")
        order = self.env["sale.order"].create({
            "partner_id": self.customer.id, "date_order": dt(1, 2),
            "order_line": [(0, 0, {"product_id": self.product.id, "product_uom_qty": 1.0})],
        })
        order.action_confirm()
        order.date_order = dt(1, 2)
        picking = order.picking_ids
        self._validate(picking, dt(1, 5))
        self.assertEqual(picking.cmc_delivery_lead_time, 3 * 1440.0)

    def test_feeder_query_count_independent_of_transfers(self):
        # AD-114: 20 more validated deliveries cost no more queries (one grouped read)
        keys = [DELIVERY_KEY]
        span = (JAN, date(2026, 2, 28))
        self.env.flush_all()
        self.env.invalidate_all()
        start = self.cr.sql_log_count
        self.Fact._cmc_refresh_feeders(*span, company=self.company, keys=keys)
        self.env.flush_all()
        budget = self.cr.sql_log_count - start + TOLERANCE
        for day in range(1, 21):
            self._ship(dt(2, day), dt(2, day, 18))
        self.env.flush_all()
        self.env.invalidate_all()
        with self.assertQueryCount(budget):
            self.Fact._cmc_refresh_feeders(*span, company=self.company, keys=keys)
            self.env.flush_all()
        self.assertEqual(self._by_month(DELIVERY_KEY)[FEB], 4320.0 + 20 * 600.0)
