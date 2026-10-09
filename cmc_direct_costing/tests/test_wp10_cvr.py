"""WP-10 DC-05 / DC-08 / DC-09 / G-1 - CVR v2 on ``cmc.cvr.analysis`` (in place, AD-101).

CONTRACT
DC-05 target profit
- New fields: ``target_basis`` Selection ``pretax`` (default = v1 behaviour, ``target_profit`` is pre-tax) /
  ``after_tax`` (``target_profit`` is net: UL = Un / (1 - t)) / ``margin_pct`` (profit = ``target_margin_pct``
  % of sales, Frost: Q = CF / (p - v - PM x p)); ``tax_rate`` Float percent, 0 <= t < 100 (ValidationError);
  ``target_margin_pct`` Float percent.
- Outputs (stored, in ``_compute_cvr``): ``target_profit_pretax`` Monetary, ``target_qty`` (existing,
  now on the pre-tax target, or the Frost formula), ``target_revenue`` Monetary = target_qty x p.
  Margin target unreachable (p - v - PM x p <= 0) -> target_qty 0 and target_revenue 0.
DC-08 sensitivity / iso-profit / A-B structures (pure methods on one analysis, single product, at ``volume``)
- ``_cmc_sensitivity(pct)`` -> list of 4 dicts in the order price, volume, variable_unit, fixed_cost:
  ``{"lever", "base_profit", "profit", "delta", "delta_pct"}`` with U = pQ - vQ - CF and the lever moved by
  +pct %.
- ``_cmc_iso_profit(lever, pct, compensate)`` -> the % change of ``compensate`` that keeps U unchanged when
  ``lever`` moves by ``pct`` %; levers: ``price`` / ``volume`` / ``variable_unit`` / ``fixed_cost``;
  unknown lever or no solution -> UserError.
- ``_cmc_compare_structure(variable_unit, fixed_cost)`` -> ``{"indifference_qty", "profit_current",
  "profit_alternative", "leverage_current", "leverage_alternative"}`` (alternative = the given structure,
  same p and Q; indifference through ``cmc_utils.indifference_qty``, None when parallel).
DC-09 multi-product with constant volume mix
- Header ``bep_qty`` of a multi-product analysis = CF / Σ α_j mc_j, α_j = qty_j / Σ qty (was 0).
- ``cmc.cvr.line`` new stored fields: ``mix_share`` Float (α_j, digits (16, 4)), ``bep_qty`` (= α_j x Q*,
  digits "Product Unit"), ``bep_revenue`` Monetary (= bep_qty x price); Σ line bep_revenue = header bep_revenue.
G-1 relevant-range warnings (WP-01 API ``cmc.cost.function._cmc_range_warning``)
- ``range_warning`` Text (stored, readonly): set by the fixed-cost derivation (create / write of its
  triggers / ``action_recompute``) when the analysis has a center: the warnings of every cost function of
  the center (same company) at ``volume`` and at ``bep_qty``, joined by newlines; False when all are
  inside their range (or no function). Functions of other centers are ignored.
"""

from datetime import date

from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestWp10Cvr(TransactionCase):
    def _cvr(self, **kw):
        vals = {"name": "wp10", "mode": "simulazione", "date_from": date(2026, 1, 1), "date_to": date(2026, 1, 31)}
        vals.update(kw)
        return self.env["cmc.cvr.analysis"].create(vals)

    # ---- DC-05 target profit --------------------------------------------------------

    def test_pretax_target_is_the_default(self):
        # DC-05: v1 behaviour kept: Qo = (Uo + CF)/(p - v) = (50,000 + 20,000)/3
        cvr = self._cvr(price_unit=10, variable_unit=7, fixed_cost=20000, target_profit=50000)
        self.assertEqual(cvr.target_basis, "pretax")
        self.assertAlmostEqual(cvr.target_profit_pretax, 50000.0, places=2)
        self.assertAlmostEqual(cvr.target_qty, 70000.0 / 3, places=2)

    def test_after_tax_target_grossed_up(self):
        # DC-05 / Cinquini II.2: Un 30,000, t 40% -> UL 50,000; RTo = 70,000 / 0.3 = 233,333.33
        cvr = self._cvr(price_unit=10, variable_unit=7, fixed_cost=20000, target_profit=30000,
                        target_basis="after_tax", tax_rate=40.0)
        self.assertAlmostEqual(cvr.target_profit_pretax, 50000.0, places=2)
        self.assertAlmostEqual(cvr.target_qty, 70000.0 / 3, places=2)
        self.assertAlmostEqual(cvr.target_revenue, 233333.33, places=2)

    def test_margin_pct_target(self):
        # DC-05 / Frost St.8: Q = 20,000 / (10 - 7 - 10% x 10) = 10,000 -> profit 10,000 = 10% of 100,000
        cvr = self._cvr(price_unit=10, variable_unit=7, fixed_cost=20000, target_basis="margin_pct", target_margin_pct=10.0)
        self.assertAlmostEqual(cvr.target_qty, 10000.0, places=2)
        self.assertAlmostEqual(cvr.target_revenue, 100000.0, places=2)
        self.assertAlmostEqual(cvr.target_profit_pretax, 10000.0, places=2)

    def test_unreachable_margin_pct(self):
        # DC-05: a 40% margin on a 30% contribution ratio cannot be reached at any volume
        cvr = self._cvr(price_unit=10, variable_unit=7, fixed_cost=20000, target_basis="margin_pct", target_margin_pct=40.0)
        self.assertEqual(cvr.target_qty, 0.0)
        self.assertEqual(cvr.target_revenue, 0.0)

    def test_tax_rate_bounds(self):
        # DC-05: t = 100% would divide by zero
        with self.assertRaises(ValidationError):
            self._cvr(price_unit=10, variable_unit=7, target_basis="after_tax", tax_rate=100.0)

    # ---- DC-08 sensitivity, iso-profit, A/B -----------------------------------------

    def _base(self):
        # p 40, v 20, Q 10,000, CF 100,000 -> U = 100,000; LO = 2
        return self._cvr(price_unit=40, variable_unit=20, fixed_cost=100000, volume=10000)

    def test_lever_table(self):
        # DC-08 / Cinquini II.3: +10% on each lever: price +40%, volume +20%, v -20%, CF -10% on profit
        table = self._base()._cmc_sensitivity(10.0)
        self.assertEqual([row["lever"] for row in table], ["price", "volume", "variable_unit", "fixed_cost"])
        for row, profit, pct in zip(table, [140000.0, 120000.0, 80000.0, 90000.0], [40.0, 20.0, -20.0, -10.0], strict=True):
            self.assertAlmostEqual(row["base_profit"], 100000.0, places=2)
            self.assertAlmostEqual(row["profit"], profit, places=2)
            self.assertAlmostEqual(row["delta"], profit - 100000.0, places=2)
            self.assertAlmostEqual(row["delta_pct"], pct, places=4)

    def test_iso_profit_fixed_cost_increase(self):
        # DC-08 / Cinquini II.3: +20% CF is covered by +5% price, -10% v or +10% volume
        cvr = self._base()
        self.assertAlmostEqual(cvr._cmc_iso_profit("fixed_cost", 20.0, "price"), 5.0, places=4)
        self.assertAlmostEqual(cvr._cmc_iso_profit("fixed_cost", 20.0, "variable_unit"), -10.0, places=4)
        self.assertAlmostEqual(cvr._cmc_iso_profit("fixed_cost", 20.0, "volume"), 10.0, places=4)

    def test_iso_profit_price_cut_needs_volume(self):
        # DC-08: -10% price -> MC 16/unit -> Q = 200,000 / 16 = 12,500 -> +25% volume
        self.assertAlmostEqual(self._base()._cmc_iso_profit("price", -10.0, "volume"), 25.0, places=4)

    def test_iso_profit_without_solution(self):
        # DC-08: -50% price leaves MC 0: no volume restores the profit
        with self.assertRaises(UserError):
            self._base()._cmc_iso_profit("price", -50.0, "volume")
        with self.assertRaises(UserError):
            self._base()._cmc_iso_profit("discount", 5.0, "volume")

    def test_compare_cost_structures(self):
        # DC-08 / Cinquini II.5: A v 8 CF 200 vs B v 6 CF 500 at p 10, Q 200 -> Q* 150, U 200 vs 300, LO 2 vs 2.67
        cvr = self._cvr(price_unit=10, variable_unit=8, fixed_cost=200, volume=200)
        res = cvr._cmc_compare_structure(6.0, 500.0)
        self.assertAlmostEqual(res["indifference_qty"], 150.0, places=4)
        self.assertAlmostEqual(res["profit_current"], 200.0, places=2)
        self.assertAlmostEqual(res["profit_alternative"], 300.0, places=2)
        self.assertAlmostEqual(res["leverage_current"], 2.0, places=4)
        self.assertAlmostEqual(res["leverage_alternative"], 800.0 / 300.0, places=4)
        self.assertIsNone(cvr._cmc_compare_structure(8.0, 500.0)["indifference_qty"])

    # ---- DC-09 multi-product volume mix ---------------------------------------------

    def test_multiproduct_breakeven_split_by_product(self):
        # DC-09 / Cinquini II.6: α 0.6/0.4, Σα mc = 5.6 -> Q* = 56,000/5.6 = 10,000 = A 6,000 + B 4,000
        cvr = self._cvr(is_multiproduct=True, fixed_cost=56000, line_ids=[
            (0, 0, {"qty": 600, "price_unit": 10, "variable_unit": 6}),
            (0, 0, {"qty": 400, "price_unit": 20, "variable_unit": 12}),
        ])
        self.assertAlmostEqual(cvr.bep_qty, 10000.0, places=2)
        self.assertAlmostEqual(cvr.bep_revenue, 140000.0, places=2)
        self.assertRecordValues(cvr.line_ids.sorted("id"), [
            {"mix_share": 0.6, "bep_qty": 6000.0, "bep_revenue": 60000.0},
            {"mix_share": 0.4, "bep_qty": 4000.0, "bep_revenue": 80000.0},
        ])
        self.assertAlmostEqual(sum(cvr.line_ids.mapped("bep_revenue")), cvr.bep_revenue, places=2)

    def test_multiproduct_mix_follows_quantities(self):
        # DC-09: a change of mix moves Q* (constant-mix assumption made explicit)
        cvr = self._cvr(is_multiproduct=True, fixed_cost=56000, line_ids=[
            (0, 0, {"qty": 600, "price_unit": 10, "variable_unit": 6}),
            (0, 0, {"qty": 400, "price_unit": 20, "variable_unit": 12}),
        ])
        cvr.line_ids.sorted("id")[0].qty = 1400  # α = 1400/1800 and 400/1800
        expected = 56000.0 / (1400.0 / 1800 * 4 + 400.0 / 1800 * 8)
        self.assertAlmostEqual(cvr.bep_qty, expected, places=2)


@tagged("post_install", "-at_install")
class TestWp10CvrRangeWarning(TransactionCase):
    """G-1: the CVR states a break-even on a linear cost model; outside the relevant range of the
    center's cost functions that statement is not valid and must say so."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        plan = cls.env["account.analytic.plan"].search([("parent_id", "=", False)]).filtered(
            lambda p: p._column_name() == "account_id"
        )[:1]
        cls.center = cls.env["account.analytic.account"].create({"name": "WP10 G1", "plan_id": plan.id, "cmc_center_type": "final"})
        cls.other = cls.env["account.analytic.account"].create({"name": "WP10 G1 other", "plan_id": plan.id, "cmc_center_type": "final"})
        cls.env["account.analytic.line"].create({"name": "fx", "account_id": cls.center.id, "amount": -6000,
                                                 "date": date(2026, 1, 15), "cmc_behavior": "fixed"})
        driver = cls.env["cmc.cost.driver"].create({"name": "WP10 G1 units", "code": "WP10G1"})
        Function = cls.env["cmc.cost.function"]
        cls.function = Function.create({"name": "G1 press line", "center_id": cls.center.id, "driver_id": driver.id,
                                        "fixed_amount": 6000, "variable_rate": 4, "range_min": 500, "range_max": 3000})
        Function.create({"name": "G1 elsewhere", "center_id": cls.other.id, "driver_id": driver.id,
                         "range_min": 1, "range_max": 2})

    def _cvr(self, volume):
        # CF derived 6,000; p 10, v 4 -> Q* = 1,000 (inside 500-3,000)
        return self.env["cmc.cvr.analysis"].create({
            "name": "G1", "mode": "consuntivo", "center_id": self.center.id, "date_from": date(2026, 1, 1),
            "date_to": date(2026, 1, 31), "price_unit": 10, "variable_unit": 4, "volume": volume,
        })

    def test_inside_range_no_warning(self):
        # G-1: volume 2,000 and Q* 1,000 inside 500-3,000 -> silent; the other center's function is ignored
        cvr = self._cvr(2000)
        self.assertAlmostEqual(cvr.bep_qty, 1000.0, places=2)
        self.assertFalse(cvr.range_warning)

    def test_volume_outside_range_warns(self):
        # G-1: volume 8,000 above 3,000 -> warning naming the function
        cvr = self._cvr(8000)
        self.assertIn("G1 press line", cvr.range_warning or "")

    def test_breakeven_outside_range_warns(self):
        # G-1: p 5.5 -> MC 1.5 -> Q* = 6,000/1.5 = 4,000 > 3,000 even with the volume inside
        cvr = self._cvr(2000)
        cvr.write({"price_unit": 5.5})
        cvr.action_recompute()
        self.assertAlmostEqual(cvr.bep_qty, 4000.0, places=2)
        self.assertIn("G1 press line", cvr.range_warning or "")

    def test_warning_clears_when_back_in_range(self):
        # G-1: a stale warning would be as misleading as a missing one
        cvr = self._cvr(8000)
        cvr.write({"volume": 2000})
        cvr.action_recompute()
        self.assertFalse(cvr.range_warning)
