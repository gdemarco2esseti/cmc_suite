from datetime import date

from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestCvr(TransactionCase):
    """TS-CVR-001/002 + TS-LO-001 — break-even, target profit, MoS, leverage."""

    def _cvr(self, **kw):
        vals = {"name": "t", "date_from": date(2026, 1, 1), "date_to": date(2026, 1, 31)}
        vals.update(kw)
        return self.env["cmc.cvr.analysis"].create(vals)

    # T1/T2 — break-even (esempio: p=10, v=4, CF=90.000 -> Q*=15.000)
    def test_break_even(self):
        a = self._cvr(price_unit=10, variable_unit=4, fixed_cost=90000)
        self.assertEqual(a.bep_qty, 15000.0)
        self.assertEqual(a.bep_revenue, 150000.0)
        self.assertEqual(a.mc_unit, 6.0)

    # T3 — utile obiettivo
    def test_target_profit(self):
        a = self._cvr(price_unit=10, variable_unit=4, fixed_cost=90000,
                      target_profit=30000)
        self.assertEqual(a.target_qty, 20000.0)

    # T4 — margine di sicurezza
    def test_margin_of_safety(self):
        a = self._cvr(price_unit=10, variable_unit=4, fixed_cost=90000, volume=18000)
        self.assertEqual(a.margin_of_safety, 30000.0)

    # T5 — MC <= 0: nessun pareggio, niente crash
    def test_no_breakeven(self):
        a = self._cvr(price_unit=4, variable_unit=4, fixed_cost=1000)
        self.assertFalse(a.has_breakeven)
        self.assertEqual(a.bep_qty, 0.0)
        self.assertEqual(a.bep_revenue, 0.0)

    # TS-LO-001 — leva operativa (MC=2400, UO=400 -> LO=6)
    def test_operating_leverage(self):
        a = self._cvr(price_unit=10, variable_unit=4, fixed_cost=2000, volume=400)
        self.assertEqual(a.operating_leverage, 6.0)

    def test_leverage_zero_uo(self):
        a = self._cvr(price_unit=10, variable_unit=4, fixed_cost=2400, volume=400)
        # UO = 2400-2400 = 0 -> leverage 0, no crash
        self.assertEqual(a.operating_leverage, 0.0)

    # TS-CVR-002 — multiprodotto: MC medio ponderato + BEP complessivo
    def test_multiproduct(self):
        a = self._cvr(
            is_multiproduct=True, fixed_cost=1000,
            line_ids=[
                (0, 0, {"qty": 100, "price_unit": 20, "variable_unit": 8}),
                (0, 0, {"qty": 100, "price_unit": 10, "variable_unit": 6}),
            ],
        )
        # tot_rev=3000, tot_mc=1600 -> ratio=0.5333 ; bep_rev=1000/ratio
        self.assertAlmostEqual(a.mc_ratio, 1600.0 / 3000.0, places=4)
        self.assertAlmostEqual(a.bep_revenue, 1000.0 / (1600.0 / 3000.0), places=2)

    # break-even curve (profittogramma) — 3 punti
    def test_curve_points(self):
        a = self._cvr(price_unit=10, variable_unit=4, fixed_cost=90000)
        a.action_recompute()
        self.assertEqual(len(a.curve_ids), 4)
        at_bep = a.curve_ids.filtered(lambda c: c.volume == 15000.0)
        self.assertEqual(at_bep.revenue, at_bep.total_cost)  # crossing at BEP

    # item 11 — a margin below the currency step is rounding noise, not a
    # margin: bare float comparisons turned it into a BEP three orders of
    # magnitude off, presented as a fact
    def test_near_zero_margin_has_no_breakeven(self):
        a = self._cvr(price_unit=10, variable_unit=9.999, fixed_cost=1000)
        self.assertFalse(a.has_breakeven)
        self.assertEqual(a.bep_qty, 0.0)
        self.assertEqual(a.bep_revenue, 0.0)
        self.assertEqual(a.target_qty, 0.0)
        self.assertFalse(a.curve_ids)

    def test_near_zero_margin_multiproduct(self):
        a = self._cvr(
            is_multiproduct=True, fixed_cost=1000,
            line_ids=[(0, 0, {"qty": 1, "price_unit": 10, "variable_unit": 9.999})],
        )
        self.assertFalse(a.has_breakeven)

    # item 17 — the curve must never show points from previous parameters
    def test_curve_follows_parameters(self):
        a = self._cvr(price_unit=10, variable_unit=4, fixed_cost=90000)
        self.assertEqual(sorted(a.curve_ids.mapped("volume"))[1], 15000.0)
        a.fixed_cost = 60000  # Q* = 10.000
        self.assertEqual(a.bep_qty, 10000.0)
        self.assertEqual(sorted(a.curve_ids.mapped("volume"))[1], 10000.0)

    def test_no_curve_without_breakeven(self):
        a = self._cvr(price_unit=4, variable_unit=4, fixed_cost=1000)
        self.assertFalse(a.curve_ids)

    # a zero break-even collapses the four samples onto the origin: no curve
    def test_no_curve_when_bep_is_zero(self):
        a = self._cvr(price_unit=10, variable_unit=4, fixed_cost=0)
        self.assertTrue(a.has_breakeven)
        self.assertEqual(a.bep_qty, 0.0)
        self.assertFalse(a.curve_ids)

    # item 12 — a CF typed in simulation mode must survive an edit of the
    # fields the old editable compute depended on
    def test_simulation_fixed_cost_survives_edits(self):
        a = self._cvr(mode="simulazione", price_unit=10, variable_unit=4,
                      fixed_cost=90000)
        a.date_to = date(2026, 2, 28)
        a.mode = "simulazione"
        self.assertEqual(a.fixed_cost, 90000.0)
        self.assertEqual(a.bep_qty, 15000.0)

    def test_margin_of_safety_on_rounded_break_even(self):
        # stored monetary figures: MoS comes from the break-even revenue at the
        # currency step (p=10.01, Q=1.5 -> revenue 15.015, BEP revenue 7.00)
        a = self._cvr(price_unit=10.01, variable_unit=0.0, fixed_cost=7.004, volume=1.5)
        self.assertEqual(a.bep_revenue, 7.0)
        self.assertAlmostEqual(a.margin_of_safety, 8.02, places=2)


@tagged("post_install", "-at_install")
class TestCvrActualMode(TransactionCase):
    """item 12 — the only path in this module that reads cmc_base analytic
    data. No @api.depends can track it, so action_recompute() must."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.plan = (
            cls.env["account.analytic.plan"]
            .search([("parent_id", "=", False)])
            .filtered(lambda p: p._column_name() == "account_id")[:1]
        )
        cls.center = cls.env["account.analytic.account"].create(
            {"name": "CVR center", "plan_id": cls.plan.id,
             "cmc_center_type": "final"}
        )

    def _cost(self, amount, behavior="fixed", day=15):
        return self.env["account.analytic.line"].create(
            {"name": "c", "account_id": self.center.id, "amount": -amount,  # AD-005: cost = negative amount
             "date": date(2026, 1, day), "cmc_behavior": behavior}
        )

    def test_actual_mode_derives_fixed_cost(self):
        self._cost(30000)
        self._cost(20000)
        self._cost(5000, behavior="variable")  # not a fixed cost
        a = self.env["cmc.cvr.analysis"].create(
            {"name": "actual", "mode": "consuntivo", "center_id": self.center.id,
             "date_from": date(2026, 1, 1), "date_to": date(2026, 1, 31),
             "price_unit": 10, "variable_unit": 4}
        )
        a.action_recompute()
        self.assertEqual(a.fixed_cost, 50000.0)
        self.assertAlmostEqual(a.bep_qty, 50000.0 / 6.0, places=2)
        self.assertTrue(a.curve_ids)

    def test_actual_mode_refreshes_stale_fixed_cost(self):
        self._cost(30000)
        a = self.env["cmc.cvr.analysis"].create(
            {"name": "actual", "mode": "consuntivo", "center_id": self.center.id,
             "date_from": date(2026, 1, 1), "date_to": date(2026, 1, 31),
             "price_unit": 10, "variable_unit": 4}
        )
        a.action_recompute()
        self.assertEqual(a.fixed_cost, 30000.0)
        self._cost(30000)  # a new analytic line no depends could track
        a.action_recompute()
        self.assertEqual(a.fixed_cost, 60000.0)
        self.assertEqual(a.bep_qty, 10000.0)

    def test_actual_mode_ignores_lines_outside_the_window(self):
        self._cost(30000)
        self._cost(99999, day=15)
        self.env["account.analytic.line"].create(
            {"name": "old", "account_id": self.center.id, "amount": -777,
             "date": date(2025, 1, 15), "cmc_behavior": "fixed"}
        )
        a = self.env["cmc.cvr.analysis"].create(
            {"name": "actual", "mode": "consuntivo", "center_id": self.center.id,
             "date_from": date(2026, 1, 1), "date_to": date(2026, 1, 31),
             "price_unit": 10, "variable_unit": 4}
        )
        a.action_recompute()
        self.assertEqual(a.fixed_cost, 129999.0)

