"""WP-09 DC-02 / PRF-06 / AD-111 / AD-114 - the profit fact table built from posted invoice lines.

CONTRACT (part of common.py): ``cmc.profit.run`` reads revenue only through the cmc_account reader (posted,
income accounts, every move type, revenue = -balance), at the grain product x commercial partner x
salesperson x month (AD-114 (2)); quantity netted by document sign; income items without product and partner
land on the company ``unassigned`` cost object; partner attributes are snapshotted (AD-106 inv.3); unit costs
from ``cmc.rate`` (version origin); each fact drills down to exactly its journal items; the run is a snapshot,
a back-dated posting shows as a reconciliation difference.
"""

from datetime import date

from odoo import Command
from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import tagged

from .common import D_FROM, D_TO, ProfitCase


@tagged("post_install", "-at_install")
class TestProfitFacts(ProfitCase):
    def test_fact_grain_product_partner_salesperson_month(self):
        # AD-114 (2): one fact per product x commercial partner x salesperson x month, never per invoice line
        run = self._run()
        self.assertEqual(len(run.fact_ids), 5)
        self.assertRecordValues(
            run.fact_ids.sorted(lambda f: (f.revenue, f.quantity)),
            [
                {"product_id": False, "partner_id": False, "salesperson_id": False, "quantity": 0.0, "revenue": 200.0},
                {"product_id": self.p2.id, "partner_id": self.c3.id, "salesperson_id": self.salesperson_2.id, "quantity": 10.0, "revenue": 500.0},
                {"product_id": self.p1.id, "partner_id": self.c1.id, "salesperson_id": self.salesperson_1.id, "quantity": 10.0, "revenue": 900.0},
                {"product_id": self.p2.id, "partner_id": self.c1.id, "salesperson_id": self.salesperson_1.id, "quantity": 18.0, "revenue": 900.0},
                {"product_id": self.p1.id, "partner_id": self.c2.id, "salesperson_id": self.salesperson_2.id, "quantity": 10.0, "revenue": 1000.0},
            ],
        )
        self.assertEqual(set(run.fact_ids.mapped("date")), {D_FROM})

    def test_credit_note_netted_on_commercial_partner(self):
        # AD-111 inv.(2): the credit note to the C1 contact nets revenue AND quantity on the C1 fact
        run = self._run()
        fact = self._fact(run, self.p2, self.c1)
        self.assertEqual(len(fact), 1)
        self.assertAlmostEqual(fact.revenue, 900.0)
        self.assertAlmostEqual(fact.quantity, 18.0)
        self.assertFalse(run.fact_ids.filtered(lambda f: f.partner_id == self.c1_buyer))

    def test_revenue_reconciles_to_gl(self):
        # AD-111: sum of the facts = GL revenue of the span (drafts and other months out)
        run = self._run()
        self.assertAlmostEqual(run.revenue_total, 3500.0)
        self.assertAlmostEqual(run.gl_revenue, 3500.0)
        self.assertAlmostEqual(sum(run.fact_ids.mapped("revenue")), 3500.0)
        gl = self.env["account.move.line"]._cmc_revenue_read_group(self.company, D_FROM, D_TO)[0][-1]
        self.assertAlmostEqual(run.revenue_total, gl)

    def test_unassigned_revenue_on_unassigned_object(self):
        # AD-111 inv.(1): revenue without product/customer lands on an explicit unassigned object
        run = self._run()
        fact = run.fact_ids.filtered(lambda f: not f.product_id and not f.partner_id)
        self.assertAlmostEqual(fact.revenue, 200.0)
        self.assertEqual(fact.customer_object_id.object_type, "unassigned")
        self.assertEqual(fact.customer_object_id.res_model, "res.company")
        self.assertEqual(fact.customer_object_id.res_id, self.company.id)

    def test_fact_cost_objects_and_attribute_snapshot(self):
        # AD-106 inv.3 / AD-109: cost objects resolved, segment/channel snapshotted at Compute
        run = self._run()
        fact = self._fact(run, self.p1, self.c2)
        self.assertEqual(fact.product_object_id, self.obj_p1)
        self.assertEqual(fact.customer_object_id, self.obj_c2)
        self.assertEqual(fact.channel_value_id, self.ch_dist)
        self.assertEqual(fact.segment_value_id, self.seg_sme)
        self.c2.cmc_attribute_value_ids = [Command.set((self.ch_direct | self.seg_key).ids)]
        fact.invalidate_recordset()
        self.assertEqual(fact.channel_value_id, self.ch_dist, "a computed fact keeps the channel it was grouped by")

    def test_list_price_discount_and_unit_price(self):
        # PRF-04: list = lst_price x qty, on-invoice discount = revenue - list (natural sign, negative)
        run = self._run()
        self.assertRecordValues(self._fact(run, self.p1, self.c1), [
            {"list_amount": 1000.0, "discount_amount": -100.0, "unit_price": 90.0},
        ])
        self.assertRecordValues(self._fact(run, self.p2, self.c1), [
            {"list_amount": 900.0, "discount_amount": 0.0, "unit_price": 50.0},
        ])

    def test_variable_cost_from_standard_rates(self):
        # AD-108 / DC-02: variable product cost = qty x variable rate of the approved version (cost negative)
        run = self._run()
        self.assertRecordValues(self._fact(run, self.p1, self.c1), [
            {"variable_unit_cost": 40.0, "full_unit_cost": 60.0, "variable_cost": -400.0, "contribution_margin": 500.0},
        ])
        self.assertRecordValues(self._fact(run, self.p2, self.c1), [
            {"variable_unit_cost": 20.0, "full_unit_cost": 30.0, "variable_cost": -360.0, "contribution_margin": 540.0},
        ])
        self.assertAlmostEqual(run.variable_cost_total, -1360.0)
        self.assertAlmostEqual(run.contribution_total, 2140.0)

    def test_missing_unit_cost_is_reported(self):
        # AD-108: a sold product without a rate is listed, never silently costed at 0
        self._invoice(self.c2, [(self.product_b, 1, 70.0, 0.0)], date(2027, 3, 18), user=self.salesperson_2)
        run = self._run()
        self.assertEqual(run.missing_cost_product_ids, self.product_b)
        self.assertAlmostEqual(self._fact(run, self.product_b, self.c2).variable_cost, 0.0)

    def test_product_sustaining_spread_by_quantity(self):
        # PRF-01: product-level ABC cost spread over the product facts by quantity (P2 140 / 28 units = 5)
        run = self._run()
        self.assertAlmostEqual(self._fact(run, self.p1, self.c1).product_sustaining_cost, -100.0)
        self.assertAlmostEqual(self._fact(run, self.p1, self.c2).product_sustaining_cost, -100.0)
        self.assertAlmostEqual(self._fact(run, self.p2, self.c1).product_sustaining_cost, -90.0)
        self.assertAlmostEqual(self._fact(run, self.p2, self.c3).product_sustaining_cost, -50.0)

    def test_salesperson_cube(self):
        # PRF-06: the facts are a cube - grouped by salesperson they reconcile to the total
        run = self._run()
        by_seller = {
            seller: revenue
            for seller, revenue in self.env["cmc.profit.fact"]._read_group(
                [("run_id", "=", run.id)], ["salesperson_id"], ["revenue:sum"]
            )
        }
        self.assertEqual(
            {seller.id: revenue for seller, revenue in by_seller.items()},
            {self.salesperson_1.id: 1800.0, self.salesperson_2.id: 1500.0, False: 200.0},
        )

    def test_drill_down_opens_exactly_the_fact_journal_items(self):
        # PRF-06 / AD-114 (2): drill-down by domain, nothing copied; the items sum to the fact
        run = self._run()
        fact = self._fact(run, self.p2, self.c1)
        action = fact.action_cmc_open_journal_items()
        self.assertEqual(action["res_model"], "account.move.line")
        items = self.env["account.move.line"].search(action["domain"])
        self.assertEqual(items.move_id, self.inv1 | self.rfd1)
        self.assertAlmostEqual(-sum(items.mapped("balance")), fact.revenue)

    def test_back_dated_posting_is_a_reconciliation_difference(self):
        # AD-111 inv.(3) / AD-113: a computed run is a snapshot; a late posting shows as a difference
        run = self._run()
        self._invoice(self.c2, [(self.p1, 1, 50.0, 0.0)], date(2027, 3, 20), user=self.salesperson_2)
        run.invalidate_recordset()
        self.assertAlmostEqual(run.revenue_total, 3500.0)
        self.assertEqual(run._cmc_reconciliation(), {"gl_revenue": 3550.0, "run_revenue": 3500.0, "difference": 50.0})

    def test_reset_deletes_results(self):
        # AD-113: reset deletes every result row
        run = self._run()
        facts = run.fact_ids
        run.action_reset_to_draft()
        self.assertEqual(run.state, "draft")
        self.assertFalse(facts.exists())
        self.assertFalse(run.customer_ids or run.product_ids or run.channel_ids)

    def test_dates_ordered(self):
        # AD-105: the span must be ordered
        with self.assertRaises(ValidationError):
            self._run(compute=False, date_from=D_TO, date_to=D_FROM)

    def test_draft_version_refused(self):
        # AD-108: unit costs of a draft version are no certified source
        draft = self.env["cmc.plan.version"].create({
            "name": "WP09 draft std", "version_type": "standard", "company_id": self.company.id,
            "date_from": date(2027, 1, 1), "date_to": date(2027, 12, 31),
        })
        run = self._run(compute=False, cost_version_id=draft.id)
        with self.assertRaises(UserError):
            run.action_compute()
        self.assertEqual(run.state, "draft")

    def test_rn001_packaging_uom_converted_to_product_uom(self):
        # RN-001: 1 Dozen of a product in Units is 12 units: quantity, list amount and variable cost in product UoM
        run = self._packaging_sale()
        line = run.fact_ids.filtered(lambda f: f.product_id == self.p5)
        self.assertRecordValues(line, [{
            "partner_id": self.c2.id, "quantity": 12.0, "revenue": 90.0, "list_amount": 120.0, "discount_amount": -30.0,
            "unit_price": 7.5, "variable_unit_cost": 8.0, "variable_cost": -96.0, "contribution_margin": -6.0,
        }])
