"""WP-09 FIN-12 (moved from WP-13 by AD-101/AD-118) - SBU reclassification: MSLC, PN figurativo, SBU cash flow.

CONTRACT (part of common.py; Fazzini 12.4-12.6)
- Shipped ``cmc_profitability.cmc_attribute_sbu``: cmc.attribute code ``SBU``, company False, target
  product.template. The SBU of a fact = the SBU value of its product template, snapshotted on the fact and the
  product row; revenue without SBU (unassigned, products without value) -> the corporate row (sbu False).
- ``sbu_ids`` -> ``cmc.profit.sbu`` per SBU value + corporate: ``revenue``, ``variable_cost``,
  ``product_sustaining_cost``, ``mslc`` (margine semilordo di contribuzione = revenue + variable +
  product-sustaining, natural sign), ``coin_open`` / ``coin_close`` / ``pfn`` (from the inputs), ``equity`` (PN
  figurativo = coin_close - pfn), ``roce`` = mslc / coin_open x 100, ``growth`` (g) = (close - open) / open x
  100, ``cash_flow`` (FCN = mslc - delta COIN = COIN_0 x (ROCE - g)), ``sustainable`` = cash_flow > 0.
  coin_open = 0 -> roce = growth = 0.
- Inputs ``sbu_line_ids`` -> ``cmc.profit.sbu.input`` (run.line mixin, locked when computed): ``sbu_value_id``,
  ``coin_open``, ``coin_close``, ``pfn``, ``capital_key`` (%). With ``fin_analysis_id`` (computed
  cmc.fin.analysis of the company) the three amounts are read from its values: ``coin`` of the year of
  date_to (close) and of the year before (open, 0 when absent) and ``pfn`` of the year of date_to, each x
  capital_key / 100 (typed amounts ignored).
"""

from odoo import Command
from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .common import ProfitCase


@tagged("post_install", "-at_install")
class TestSbu(ProfitCase):
    def setUp(self):
        super().setUp()
        # FIN-12: the SBU attribute is shipped data of cmc_profitability (the fixture assigns it when present)
        self.assertTrue(self.sbu_attribute, "cmc_profitability must ship cmc_profitability.cmc_attribute_sbu")

    def _sbu_lines(self, **extra):
        return [
            Command.create({"sbu_value_id": self.sbu_a.id, "coin_open": 6000.0, "coin_close": 6600.0, "pfn": 2000.0, **extra}),
            Command.create({"sbu_value_id": self.sbu_b.id, "coin_open": 3500.0, "coin_close": 4900.0, "pfn": 1000.0, **extra}),
        ]

    def test_sbu_attribute_shipped(self):
        # AD-109: the SBU attribute is shipped data on product templates
        attribute = self.env.ref("cmc_profitability.cmc_attribute_sbu")
        self.assertEqual(attribute.code, "SBU")
        self.assertFalse(attribute.company_id)
        self.assertIn("product.template", attribute.target_model_ids.mapped("model"))

    def test_sbu_mslc_and_cash_flow(self):
        # FIN-12 (Fazzini 12.5-12.6): MSLC per SBU, PN figurativo, FCN = MSLC - dCOIN, ROCE vs g
        run = self._run(sbu_line_ids=self._sbu_lines())
        rows = {r.sbu_value_id: r for r in run.sbu_ids}
        self.assertEqual(set(rows), {self.sbu_a, self.sbu_b, self.env["cmc.attribute.value"]})
        self.assertRecordValues(rows[self.sbu_a] | rows[self.sbu_b], [
            {"revenue": 1900.0, "variable_cost": -800.0, "product_sustaining_cost": -200.0, "mslc": 900.0,
             "coin_open": 6000.0, "coin_close": 6600.0, "pfn": 2000.0, "equity": 4600.0, "roce": 15.0, "growth": 10.0,
             "cash_flow": 300.0, "sustainable": True},
            {"revenue": 1400.0, "variable_cost": -560.0, "product_sustaining_cost": -140.0, "mslc": 700.0,
             "coin_open": 3500.0, "coin_close": 4900.0, "pfn": 1000.0, "equity": 3900.0, "roce": 20.0, "growth": 40.0,
             "cash_flow": -700.0, "sustainable": False},
        ])
        corporate = rows[self.env["cmc.attribute.value"]]
        self.assertAlmostEqual(corporate.revenue, 200.0)
        self.assertAlmostEqual(corporate.mslc, 200.0)
        self.assertAlmostEqual(sum(run.sbu_ids.mapped("revenue")), run.revenue_total)

    def test_sbu_snapshot_on_facts(self):
        # AD-106 inv.3: the SBU a fact was grouped by stays on it
        run = self._run(sbu_line_ids=self._sbu_lines())
        self.assertEqual(self._fact(run, self.p1, self.c1).sbu_value_id, self.sbu_a)
        self.assertEqual(self._product(run, self.p2).sbu_value_id, self.sbu_b)

    def test_sbu_inputs_locked_when_computed(self):
        # AD-113: inputs of a computed run cannot change
        run = self._run(sbu_line_ids=self._sbu_lines())
        with self.assertRaises(UserError):
            run.sbu_line_ids[0].coin_close = 1.0

    def test_sbu_capital_from_financial_analysis(self):
        # FIN-12 + AD-102: COIN and PFN come from the cmc_fin_analysis run, split by the SBU capital key
        fin = self.env["cmc.fin.analysis"].create({"name": "WP09 fin", "company_id": self.company.id, "year_from": 2026, "year_to": 2027})
        fin.action_compute()
        values = {(v.code, v.year): v.value for v in fin.value_ids}
        coin_close, coin_open, pfn = values[("coin", 2027)], values.get(("coin", 2026), 0.0), values[("pfn", 2027)]
        self.assertTrue(coin_close, "guard: the fixture GL gives a non-zero COIN")
        run = self._run(fin_analysis_id=fin.id, sbu_line_ids=[
            Command.create({"sbu_value_id": self.sbu_a.id, "capital_key": 60.0}),
            Command.create({"sbu_value_id": self.sbu_b.id, "capital_key": 40.0}),
        ])
        row = run.sbu_ids.filtered(lambda r: r.sbu_value_id == self.sbu_a)
        self.assertAlmostEqual(row.coin_close, 0.6 * coin_close, places=2)
        self.assertAlmostEqual(row.coin_open, 0.6 * coin_open, places=2)
        self.assertAlmostEqual(row.pfn, 0.6 * pfn, places=2)
        with self.assertRaises(UserError):
            fin.action_reset_to_draft()
