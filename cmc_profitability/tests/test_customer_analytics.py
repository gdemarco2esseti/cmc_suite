"""WP-09 PRF-03 / PRF-04 / PRF-05 - whale curve, quadrants, price waterfall, surcharge what-if, supplier TCO.

CONTRACT (part of common.py)
- Whale (PRF-03, Cokins Ch3 / Kaplan Ch9-10): partner rows ranked by ``customer_profit`` desc (rank 1..n,
  unassigned rank 0 and outside the curve); ``cumulative_profit`` running sum, ``cumulative_profit_pct`` =
  cumulative / ``whale_total`` x 100; run ``whale_total`` (sum of ranked profits = last point), ``whale_peak``
  (max cumulative), ``whale_unrealized`` = peak - total. Products ranked the same way on ``product_margin``.
- Quadrant (PRF-03, Kaplan Ch10): ``net_margin_pct`` = product_margin / revenue x 100, ``cost_to_serve_pct`` =
  -cost_to_serve / revenue x 100; thresholds = the same ratios over all ranked customers (run
  ``quadrant_margin_threshold`` / ``quadrant_cost_threshold``); ``>=`` threshold is high:
  passive (high margin, low cts) / carriage (high, high) / bargain (low, low) / aggressive (low, high).
- Waterfall (PRF-04): customer ``list_amount``, ``discount_amount`` (on-invoice, negative), revenue = invoice
  price, ``off_invoice_amount`` = sum of the ``cmc.profit.waterfall`` rows (price elements: posted items of the
  element accounts with that partner in span, -balance), ``pocket_amount`` = revenue + off_invoice_amount.
  A price element on an income account -> ValidationError (already in revenue).
- ``run._cmc_whatif_surcharge(surcharge, min_order_amount=0.0)`` -> {partner id: customer_profit + surcharge x
  posted out_invoice of the span with untaxed amount < min_order_amount (0 = every invoice)}; read-only.
- TCO (PRF-05, Kaplan Ch11): supplier rows: ``purchase_amount`` (posted vendor bill product lines of the span,
  negative), ``activity_cost`` (ABC supplier objects), ``tco`` = sum, ``tco_index`` = tco / purchase_amount,
  ``rank`` by tco_index ascending (cost, not price).
"""

from odoo import Command
from odoo.exceptions import ValidationError
from odoo.tests.common import tagged

from .common import ProfitCase


@tagged("post_install", "-at_install")
class TestCustomerAnalytics(ProfitCase):
    def test_whale_curve_customers(self):
        # PRF-03: ranked cumulative profit peaks above 100% and ends at the reported profit
        run = self._run()
        self.assertRecordValues(
            self._customer(run, self.c1) | self._customer(run, self.c2) | self._customer(run, self.c3),
            [
                {"rank": 1, "cumulative_profit": 500.0, "cumulative_profit_pct": 125.0},
                {"rank": 2, "cumulative_profit": 700.0, "cumulative_profit_pct": 175.0},
                {"rank": 3, "cumulative_profit": 400.0, "cumulative_profit_pct": 100.0},
            ],
        )
        self.assertEqual(self._customer(run, self.env["res.partner"]).rank, 0)
        self.assertRecordValues(run, [{"whale_total": 400.0, "whale_peak": 700.0, "whale_unrealized": 300.0}])

    def test_whale_curve_products(self):
        # PRF-03: the same curve on products (product margin)
        run = self._run()
        self.assertRecordValues(self._product(run, self.p1) | self._product(run, self.p2), [
            {"rank": 1, "cumulative_profit_pct": 56.25},
            {"rank": 2, "cumulative_profit_pct": 100.0},
        ])

    def test_quadrant_net_margin_vs_cost_to_serve(self):
        # PRF-03 (Kaplan Ch10 2x2): thresholds 1600/3300 margin and 1000/3300 cost-to-serve
        run = self._run()
        self.assertAlmostEqual(run.quadrant_margin_threshold, 1600.0 / 3300.0 * 100, places=2)
        self.assertAlmostEqual(run.quadrant_cost_threshold, 1000.0 / 3300.0 * 100, places=2)
        c1, c2, c3 = self._customer(run, self.c1), self._customer(run, self.c2), self._customer(run, self.c3)
        self.assertAlmostEqual(c1.net_margin_pct, 850.0 / 1800.0 * 100, places=2)
        self.assertAlmostEqual(c3.cost_to_serve_pct, 100.0, places=2)
        self.assertEqual((c1.quadrant, c2.quadrant, c3.quadrant), ("bargain", "passive", "carriage"))

    def test_price_waterfall(self):
        # PRF-04: list -> invoice (on-invoice discount) -> pocket (off-invoice elements)
        run = self._run()
        self.assertRecordValues(self._customer(run, self.c1) | self._customer(run, self.c2) | self._customer(run, self.c3), [
            {"list_amount": 1900.0, "discount_amount": -100.0, "revenue": 1800.0, "off_invoice_amount": -60.0, "pocket_amount": 1740.0},
            {"list_amount": 1000.0, "discount_amount": 0.0, "revenue": 1000.0, "off_invoice_amount": 0.0, "pocket_amount": 1000.0},
            {"list_amount": 500.0, "discount_amount": 0.0, "revenue": 500.0, "off_invoice_amount": -40.0, "pocket_amount": 460.0},
        ])
        rows = {(w.partner_id, w.element_id): w.amount for w in run.waterfall_ids}
        self.assertEqual(rows, {(self.c1, self.el_freight): -60.0, (self.c3, self.el_rebate): -40.0})

    def test_price_element_on_income_account_refused(self):
        # PRF-04: an income account is already revenue - as an element it would be counted twice
        with self.assertRaises(ValidationError):
            self.env["cmc.profit.price.element"].create({
                "name": "Bad", "code": "BAD", "company_id": self.company.id,
                "account_ids": [Command.set(self.revenue_account.ids)],
            })

    def test_order_count_and_surcharge_whatif(self):
        # PRF-04: cost-to-serve menu pricing - what if each (small) order paid a surcharge
        run = self._run()
        self.assertEqual(
            [self._customer(run, p).order_count for p in (self.c1, self.c2, self.c3)], [1, 1, 1]
        )
        self.assertEqual(run._cmc_whatif_surcharge(50.0), {self.c1.id: 550.0, self.c2.id: 250.0, self.c3.id: -250.0})
        self.assertEqual(run._cmc_whatif_surcharge(50.0, min_order_amount=600.0), {self.c1.id: 500.0, self.c2.id: 200.0, self.c3.id: -250.0})
        self.assertAlmostEqual(self._customer(run, self.c3).customer_profit, -300.0, msg="a what-if never writes the run")

    def test_supplier_tco_ranks_by_cost_not_price(self):
        # PRF-05 (Kaplan Ch11 IC 4.76 vs 3.17): the cheaper price is the dearer supplier
        run = self._run()
        rows = {r.partner_id: r for r in run.supplier_ids}
        self.assertEqual(set(rows), {self.v1, self.v2})
        self.assertRecordValues(rows[self.v1] | rows[self.v2], [
            {"cost_object_id": self.obj_v1.id, "purchase_amount": -1000.0, "activity_cost": -50.0, "tco": -1050.0, "rank": 1},
            {"cost_object_id": self.obj_v2.id, "purchase_amount": -950.0, "activity_cost": -150.0, "tco": -1100.0, "rank": 2},
        ])
        self.assertAlmostEqual(rows[self.v1].tco_index, 1.05, places=4)
        self.assertAlmostEqual(rows[self.v2].tco_index, 1100.0 / 950.0, places=4)

    def test_vendor_refund_nets_purchase(self):
        # AD-111 inv.(2): a vendor refund nets the purchase amount, never filtered out
        self._invoice(self.v2, [(self.product_a, 1, 50.0, 0.0)], self.vb2.date, move_type="in_refund")
        run = self._run()
        row = run.supplier_ids.filtered(lambda r: r.partner_id == self.v2)
        self.assertAlmostEqual(row.purchase_amount, -900.0)
