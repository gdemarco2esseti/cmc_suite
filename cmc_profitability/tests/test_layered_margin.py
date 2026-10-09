"""WP-09 PRF-01 / PRF-02 - layered margins along the sustaining hierarchy and the customer P&L.

CONTRACT (part of common.py): M1 contribution = revenue + variable product cost; M2 product margin = M1 +
product-sustaining (ABC product-level flows, spread by quantity); M3 served margin = M2 + cost-to-serve (ABC
customer objects); M4 customer profit = M3 + channel cost (ABC channel objects spread over the channel
customers pro rata revenue); business-sustaining stays at company level: operating profit = sum M4 +
sustaining; capital charge = -AR balance at date_to x capital_rate / 100 x months / 12; economic profit =
operating profit + capital charge. Costs negative (AD-112). Reconciliation: sum of customers = sum of products
= sum of channels = GL revenue; assigned cost <= ledger cost (fail loud otherwise); the run records the usage
of the ABC run and of the version (AD-002).
"""

from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .common import D_FROM, D_TO, ProfitCase


@tagged("post_install", "-at_install")
class TestLayeredMargin(ProfitCase):
    def test_fixture_producers(self):
        # guard of the hand-computed fixture: the producers (ABC run, GL) give the figures the tests rely on
        res = {r.cost_object_id: r.amount for r in self.abc_run.result_ids}
        self.assertEqual(
            {obj: round(res.get(obj, 0.0), 2) for obj in (self.obj_p1, self.obj_p2, self.obj_c1, self.obj_c2, self.obj_c3,
                                                         self.obj_direct, self.obj_dist, self.obj_v1, self.obj_v2)},
            {self.obj_p1: 200.0, self.obj_p2: 140.0, self.obj_c1: 300.0, self.obj_c2: 200.0, self.obj_c3: 500.0,
             self.obj_direct: 50.0, self.obj_dist: 150.0, self.obj_v1: 50.0, self.obj_v2: 150.0},
        )
        self.assertAlmostEqual(self.abc_run.sustaining_cost, 205.0)
        self.assertAlmostEqual(self.env["account.move.line"]._cmc_revenue_read_group(self.company, D_FROM, D_TO)[0][-1], 3500.0)

    def test_customer_layers(self):
        # PRF-02 (Kanthal shape): net price - product cost - cost-to-serve layers, costs at their causal level
        run = self._run()
        self.assertRecordValues(
            self._customer(run, self.c1) | self._customer(run, self.c2) | self._customer(run, self.c3),
            [
                {"revenue": 1800.0, "variable_cost": -760.0, "contribution_margin": 1040.0, "product_sustaining_cost": -190.0,
                 "product_margin": 850.0, "cost_to_serve": -300.0, "served_margin": 550.0, "channel_cost": -50.0,
                 "customer_profit": 500.0},
                {"revenue": 1000.0, "variable_cost": -400.0, "contribution_margin": 600.0, "product_sustaining_cost": -100.0,
                 "product_margin": 500.0, "cost_to_serve": -200.0, "served_margin": 300.0, "channel_cost": -100.0,
                 "customer_profit": 200.0},
                {"revenue": 500.0, "variable_cost": -200.0, "contribution_margin": 300.0, "product_sustaining_cost": -50.0,
                 "product_margin": 250.0, "cost_to_serve": -500.0, "served_margin": -250.0, "channel_cost": -50.0,
                 "customer_profit": -300.0},
            ],
        )

    def test_customer_snapshot_dimensions(self):
        # AD-106 inv.3: the customer row carries its cost object and the attributes it was grouped by
        run = self._run()
        self.assertRecordValues(self._customer(run, self.c3), [
            {"cost_object_id": self.obj_c3.id, "channel_value_id": self.ch_dist.id, "segment_value_id": self.seg_sme.id},
        ])

    def test_unassigned_customer_row(self):
        # AD-111 inv.(1): the unassigned row keeps the revenue the P&L has and no customer has
        run = self._run()
        row = self._customer(run, self.env["res.partner"])
        self.assertEqual(len(row), 1)
        self.assertRecordValues(row, [{
            "revenue": 200.0, "contribution_margin": 200.0, "product_margin": 200.0, "served_margin": 200.0,
            "customer_profit": 200.0, "capital_charge": 0.0, "economic_profit": 200.0,
        }])
        self.assertEqual(row.cost_object_id.object_type, "unassigned")

    def test_capital_charge_and_economic_profit(self):
        # PRF-02 / Cokins Ch5: capital charge = AR balance x rate (12%/year, one month) -> economic profit
        run = self._run()
        self.assertRecordValues(
            self._customer(run, self.c1) | self._customer(run, self.c2) | self._customer(run, self.c3),
            [
                {"receivable_balance": 1800.0, "capital_charge": -18.0, "economic_profit": 482.0},
                {"receivable_balance": 1000.0, "capital_charge": -10.0, "economic_profit": 190.0},
                {"receivable_balance": 500.0, "capital_charge": -5.0, "economic_profit": -305.0},
            ],
        )

    def test_company_layers(self):
        # PRF-01: the layered statement closes on operating and economic profit of the company
        run = self._run()
        self.assertRecordValues(run, [{
            "revenue_total": 3500.0, "variable_cost_total": -1360.0, "contribution_total": 2140.0,
            "product_sustaining_total": -340.0, "cost_to_serve_total": -1000.0, "channel_cost_total": -200.0,
            "business_sustaining_cost": -205.0, "operating_profit": 395.0, "capital_charge_total": -33.0,
            "economic_profit": 362.0,
        }])
        self.assertAlmostEqual(sum(run.customer_ids.mapped("customer_profit")) + run.business_sustaining_cost, run.operating_profit)

    def test_product_layers(self):
        # PRF-01: product P&L - M1 and M2 per product, unassigned row included
        run = self._run()
        self.assertRecordValues(self._product(run, self.p1) | self._product(run, self.p2), [
            {"quantity": 20.0, "revenue": 1900.0, "variable_cost": -800.0, "contribution_margin": 1100.0,
             "product_sustaining_cost": -200.0, "product_margin": 900.0, "cost_object_id": self.obj_p1.id},
            {"quantity": 28.0, "revenue": 1400.0, "variable_cost": -560.0, "contribution_margin": 840.0,
             "product_sustaining_cost": -140.0, "product_margin": 700.0, "cost_object_id": self.obj_p2.id},
        ])
        self.assertAlmostEqual(self._product(run, self.env["product.product"]).revenue, 200.0)

    def test_channel_layers(self):
        # PRF-01: channel level - served margin of its customers minus the channel cost
        run = self._run()
        rows = {r.channel_value_id: r for r in run.channel_ids}
        self.assertEqual(set(rows), {self.ch_direct, self.ch_dist, self.env["cmc.attribute.value"]})
        self.assertRecordValues(rows[self.ch_direct] | rows[self.ch_dist], [
            {"revenue": 1800.0, "served_margin": 550.0, "channel_cost": -50.0, "channel_margin": 500.0},
            {"revenue": 1500.0, "served_margin": 50.0, "channel_cost": -150.0, "channel_margin": -100.0},
        ])

    def test_every_view_reconciles_to_gl_revenue(self):
        # PRF-01 / PRF-06: each summary reconciles to the base rows and to the P&L revenue
        run = self._run()
        for rows in (run.fact_ids, run.customer_ids, run.product_ids, run.channel_ids):
            self.assertAlmostEqual(sum(rows.mapped("revenue")), run.gl_revenue)

    def test_assigned_cost_within_ledger(self):
        # AD-113 / INT-05: what the run assigns is part of the ledger; the rest is reported, never charged
        run = self._run()
        self.assertRecordValues(run, [{"ledger_cost": -2105.0, "assigned_cost": -1945.0, "unassigned_cost": -160.0}])

    def test_assigned_cost_above_ledger_fails_loud(self):
        # AD-113: the ABC snapshot assigns more than the ledger now holds -> conservation broken, fail loud
        self.env["account.analytic.line"].search([("account_id", "=", self.s.id)]).unlink()
        run = self._run(compute=False)
        with self.assertRaises(UserError):
            run.action_compute()
        self.assertEqual(run.state, "draft")
        self.assertFalse(run.fact_ids)

    def test_abc_run_must_be_computed_and_inside_span(self):
        # AD-002 / AD-113: a draft ABC run, or one outside the span, is no certified source
        self.abc_run.action_reset_to_draft()
        run = self._run(compute=False)
        with self.assertRaises(UserError):
            run.action_compute()
        self.abc_run.action_compute()
        february = self._run(compute=False, date_from=D_FROM.replace(month=2), date_to=D_FROM.replace(day=28, month=2))
        with self.assertRaises(UserError):
            february.action_compute()

    def test_sources_locked_while_run_computed(self):
        # AD-002: the ABC run and the version read by a computed run cannot be reset
        self._run()
        with self.assertRaises(UserError):
            self.abc_run.action_reset_to_draft()
        with self.assertRaises(UserError):
            self.version.action_reset_to_draft()

    def test_without_abc_run_layers_are_empty(self):
        # PRF-01: the ABC run is optional - without it only M1 is computed, every ABC layer is 0
        run = self._run(abc_run_id=False)
        self.assertRecordValues(run, [{"contribution_total": 2140.0, "cost_to_serve_total": 0.0, "operating_profit": 2140.0,
                                       "assigned_cost": 0.0, "ledger_cost": -2105.0}])
