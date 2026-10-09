"""WP-09 AD-114 - access cost of the profitability Compute does not grow with invoices, customers or products.

CONTRACT
- ``account.move.line`` and the profit facts are unbounded (AD-114): revenue, quantities, receivables, price
  elements and vendor bills are read with grouped reads at the fact grain (product x commercial partner x
  salesperson x month) or per partner; partner/product attributes are read in one query per attribute
  (``_cmc_group_by_attribute``); cost objects resolved in one batch (``_cmc_ensure_objects``); unit cost rates
  read in one search for all products (not one ``_cmc_lookup`` per product); results created in batches.
- Measured: the Compute of a month with 30 customers / 30 products / 30 invoices (60 lines) + 10 vendor bills
  costs the same queries as a month with 3 / 3 / 3 + 1, within a small constant tolerance. A per-line, per-fact,
  per-partner or per-product query = N+1 = red.
"""

from datetime import date

from odoo import Command
from odoo.tests.common import tagged

from .common import ProfitCase

TOLERANCE = 5


@tagged("post_install", "-at_install")
class TestPerformance(ProfitCase):
    def _volume_run(self, n, month):
        day = date(2027, month, 10)
        first, last = date(2027, month, 1), date(2027, month, 28)
        partners = self.env["res.partner"].create([
            {"name": "WP09 vol %s-%s" % (n, i), "is_company": True,
             "cmc_attribute_value_ids": [Command.set((self.ch_direct if i % 2 else self.ch_dist).ids + self.seg_sme.ids)]}
            for i in range(n)
        ])
        products = self.env["product.product"].create([{"name": "WP09 vol P %s-%s" % (n, i), "list_price": 10.0} for i in range(n + 1)])
        objects = self.env["cmc.cost.object"]._cmc_ensure_objects(products, company=self.company)
        version = self.env["cmc.plan.version"].create({
            "name": "WP09 vol std %s" % n, "version_type": "standard", "company_id": self.company.id,
            "date_from": first, "date_to": last,
        })
        self.env["cmc.rate"]._cmc_publish(version, [
            {"holder": obj, "driver": self.units, "component": component, "date": first, "value": value}
            for obj in objects for component, value in (("variable", 4.0), ("total", 6.0))
        ])
        version.action_approve()
        for i, partner in enumerate(partners):
            seller = self.salesperson_1 if i % 2 else self.salesperson_2
            self._invoice(partner, [(products[i], 2, 10.0, 0.0), (products[i + 1], 1, 9.0, 0.0)], day, user=seller)
            self._entry(day, [(self.freight_account, 1.0, partner), (self.bank_account, -1.0, False)])
        for i in range(max(1, n // 3)):
            self._invoice(partners[i], [(self.product_a, 1, 100.0, 0.0)], day, move_type="in_invoice")
        return self._run(compute=False, name="WP09 vol %s" % n, date_from=first, date_to=last, abc_run_id=False,
                         cost_version_id=version.id)

    def test_compute_query_count_independent_of_volume(self):
        # AD-114: 30 customers/products/invoices must cost the same queries as 3
        small = self._volume_run(3, 5)
        big = self._volume_run(30, 6)
        self.env.flush_all()
        self.env.invalidate_all()
        start = self.cr.sql_log_count
        small.action_compute()
        self.env.flush_all()
        budget = self.cr.sql_log_count - start + TOLERANCE
        self.env.invalidate_all()
        with self.assertQueryCount(budget):
            big.action_compute()
            self.env.flush_all()
        # 30 invoices x (2 x 10 + 9) = 870
        self.assertAlmostEqual(big.revenue_total, 870.0)
        self.assertEqual(len(big.customer_ids.filtered("partner_id")), 30)
        self.assertEqual(len(big.supplier_ids), 10)
