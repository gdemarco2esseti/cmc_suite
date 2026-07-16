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
        self.assertAlmostEqual(a.leverage_impact(0.3333), 2.0, places=2)

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
