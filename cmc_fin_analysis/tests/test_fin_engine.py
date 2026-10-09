"""WP-13 engine: reclassification, history import, ratios, cash flows, crisis, value (FIN-01..FIN-11).

CONTRACT: see ``tests/common.py``. Expected figures are hand-computed from the fixture there
(3 fiscal years: 2024 imported history, 2025-2026 GL). Values are presentation values (assets,
liabilities, revenues and costs positive), ratios are raw numbers (0.135, not 13.5).
"""

from datetime import date

from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import FinAnalysisCommon


@tagged("post_install", "-at_install")
class TestFinEngine(FinAnalysisCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.analysis = cls._run(2024, 2026, ateco_code="25.62")
        cls.analysis.action_compute()

    # ---- FIN-01 balance sheet -------------------------------------------------------------------

    def test_bs_financial_criterion_2026(self):
        # FIN-01: SP finanziario, current = within 12 months; PN includes the year and undistributed results
        self.assertValues(self.analysis, 2026, {
            "intangible": 6000, "tangible": 220000, "other_op_nc": 0, "fin_assets_nc": 25000,
            "inv_raw": 33000, "inv_finished": 30000, "inventory": 63000, "receivables": 150000,
            "other_op_c": 11000, "fin_assets_c": 5000, "cash": 77000,
            "assets_nc": 251000, "assets_c": 306000, "total_assets": 557000,
            "equity": 274000, "op_liab_nc": 50000, "fin_debt_nc": 90000, "liab_nc": 140000,
            "payables": 95000, "other_op_liab_c": 28000, "fin_debt_c": 20000, "liab_c": 143000,
            "total_sources": 557000,
        })

    def test_bs_functional_aggregates_and_identity_every_year(self):
        # FIN-01: Imm. netto, CCNop, COIN, CIN, PFN and the identity COIN = PN + PFN, on all 3 years
        expected = {
            2024: {"total_assets": 430000, "equity": 120000, "coin": 250000, "cin": 290000, "pfn": 130000, "cash": 15000},
            2025: {"total_assets": 488000, "equity": 188000, "net_fixed": 173000, "ccnop": 89000, "coin": 262000,
                   "fin_assets": 66000, "cin": 328000, "fin_debt": 140000, "pfn": 74000, "cash": 41000},
            2026: {"op_assets_nc": 226000, "net_fixed": 176000, "op_assets_c": 224000, "op_liab_c": 123000,
                   "ccnop": 101000, "coin": 277000, "fin_assets": 107000, "cin": 384000, "fin_debt": 110000,
                   "pfn": 3000, "pfn_nc": 65000, "pfn_c": -62000},
        }
        for year, values in expected.items():
            self.assertValues(self.analysis, year, values)
            self.assertAlmostEqual(self._val(self.analysis, "coin", year),
                                   self._val(self.analysis, "equity", year) + self._val(self.analysis, "pfn", year), places=2)
            self.assertAlmostEqual(self._val(self.analysis, "total_assets", year), self._val(self.analysis, "total_sources", year), places=2)

    def test_every_account_line_has_a_row_every_year(self):
        # result snapshot: an empty line is a 0 row, not a missing row (stable multi-year table)
        bs = self.schemes["scheme_bs_functional"]
        for year in (2024, 2025, 2026):
            for line in bs.line_ids.filtered(lambda line: line.line_type == "account"):
                self._row(self.analysis, line.code, year)
        self.assertEqual(self._val(self.analysis, "provisions", 2026), 0.0)

    # ---- AD-116 historical import ---------------------------------------------------------------

    def test_history_year_wins_over_gl(self):
        # AD-116: a year with imported balances ignores the GL (the 999 noise entry of 2024)
        self.assertEqual(self._row(self.analysis, "revenue", 2024).source, "history")
        self.assertEqual(self._row(self.analysis, "revenue", 2025).source, "gl")
        self.assertValues(self.analysis, 2024, {"revenue": 1000000, "services": 200000})

    def test_history_import_through_standard_load(self):
        # AD-116: prior years come from a file through the standard import (account by code)
        result = self.env["cmc.fin.history"].load(
            ["year", "account_id", "balance"],
            [["2022", "FXBNK", "5000"], ["2022", "FXCAP", "-5000"]],
        )
        self.assertFalse(result["messages"], result["messages"])
        rows = self.env["cmc.fin.history"].search([("year", "=", 2022), ("company_id", "=", self.company.id)])
        self.assertEqual(rows.account_id, self.acc["FXBNK"] | self.acc["FXCAP"])
        run = self._run(2022, 2022)
        run.action_compute()
        self.assertValues(run, 2022, {"cash": 5000, "equity": 5000, "total_assets": 5000})
        self.assertEqual(self._row(run, "cash", 2022).source, "history")

    def test_unbalanced_history_year_blocks_compute(self):
        # AD-116: an imported trial balance that does not sum to zero cannot produce a balance sheet
        self.env["cmc.fin.history"].create({"company_id": self.company.id, "year": 2023, "account_id": self.acc["FXBNK"].id, "balance": 1000})
        with self.assertRaises(UserError):
            self._run(2023, 2023).action_compute()

    # ---- FIN-03 / FIN-04 P&L --------------------------------------------------------------------

    def test_pl_value_added_2026(self):
        # FIN-03: VP - external costs = VA - personnel = MOL - non-cash = MON +/- financial = ... = net income
        self.assertValues(self.analysis, 2026, {
            "revenue": 1200000, "fg_inv_change": 6000, "production_value": 1206000, "purchases": 480000,
            "raw_inv_change": 3000, "services": 220000, "external_costs": 703000, "value_added": 503000,
            "personnel": 270000, "ebitda": 233000, "severance": 24000, "depreciation": 47000, "non_cash": 71000,
            "mon": 162000, "fin_income": 2000, "ebit": 164000, "fin_charges": 8000, "ordinary_result": 156000,
            "pre_tax": 156000, "taxes": 40000, "net_income": 116000, "raw_consumption": 483000,
            "gross_cash_flow": 187000,
        })

    def test_pl_value_added_earlier_years(self):
        # FIN-03 / FIN-05: same scheme on the history year and on the first GL year
        self.assertValues(self.analysis, 2024, {"value_added": 400000, "ebitda": 150000, "mon": 90000, "net_income": 60000})
        self.assertValues(self.analysis, 2025, {"raw_inv_change": -6000, "value_added": 450000, "ebitda": 190000,
                                           "mon": 126000, "net_income": 88000})

    def test_pl_cost_of_sales_2026(self):
        # FIN-04: R - COGS = industrial margin - overheads = operating result; reconciles to net income
        self.assertValues(self.analysis, 2026, {
            "cs_revenue": 1200000, "cs_cogs": 818000, "cs_industrial_margin": 382000, "cs_overheads": 220000,
            "cs_operating_result": 162000, "cs_other": -46000, "cs_net_income": 116000,
        })

    def test_pl_contribution_uses_account_behavior_2026(self):
        # FIN-04 / AD-116: variable vs fixed from the cmc_account classification rules
        self.assertValues(self.analysis, 2026, {
            "cm_revenue": 1206000, "cm_variable": 483000, "cm_margin": 723000, "cm_fixed": 561000,
            "cm_operating_result": 162000, "cm_other": -46000, "cm_net_income": 116000,
        })

    # ---- FIN-05 multi-year -------------------------------------------------------------------------

    def test_common_size_and_year_on_year(self):
        # FIN-05: row / base of its scheme; var% = (X_t - X_t-1) / X_t-1
        self.assertAlmostEqual(self._row(self.analysis, "value_added", 2026).common_size_pct, 503000 / 1200000 * 100, places=4)
        self.assertAlmostEqual(self._row(self.analysis, "revenue", 2026).common_size_pct, 100.0, places=4)
        self.assertAlmostEqual(self._row(self.analysis, "receivables", 2026).common_size_pct, 150000 / 557000 * 100, places=4)
        revenue_2026 = self._row(self.analysis, "revenue", 2026)
        self.assertTrue(revenue_2026.yoy_computable)
        self.assertAlmostEqual(revenue_2026.yoy_pct, 100000 / 1100000 * 100, places=4)
        self.assertAlmostEqual(self._row(self.analysis, "revenue", 2025).yoy_pct, 10.0, places=4)
        self.assertFalse(self._row(self.analysis, "revenue", 2024).yoy_computable)
        self.assertFalse(self._row(self.analysis, "other_op_nc", 2026).yoy_computable)

    def test_sector_benchmark_attached_to_ratio(self):
        # FIN-05: imported ATECO benchmark sits next to the company ratio
        roe = self._ratio(self.analysis, "roe", 2026)
        self.assertTrue(roe.has_benchmark)
        self.assertAlmostEqual(roe.benchmark, 0.15)
        self.assertFalse(self._ratio(self.analysis, "roe", 2025).has_benchmark)

    # ---- FIN-06..FIN-08 ratios ----------------------------------------------------------------------

    def test_liquidity_ratios_2026(self):
        # FIN-06: margine di tesoreria, CCN, quick/current ratio, DSO/DPO/DIO and commercial cycle
        dso, dpo = 45.0, 360 * 95000 / 700000
        dio_raw, dio_finished = 360 * 33000 / 483000, 360 * 30000 / 818000
        self.assertRatios(self.analysis, 2026, {
            "treasury_margin": 100000, "quick_ratio": 243000 / 143000, "nwc": 163000,
            "current_ratio": 306000 / 143000, "dso": dso, "dpo": dpo, "dio_raw": dio_raw,
            "dio_finished": dio_finished, "commercial_cycle": dso + dio_raw + dio_finished - dpo,
            "cash_coverage": 77000 / 1200000,
        })

    def test_solidity_ratios_2026(self):
        # FIN-07: autonomy, D/E on PFN, structure margin, coverage of fixed assets and of interest
        self.assertRatios(self.analysis, 2026, {
            "autonomy": 274000 / 557000, "debt_equity": 3000 / 274000, "structure_margin": 23000,
            "fixed_asset_coverage": 274000 / 251000, "interest_coverage": 29.125, "debt_coverage": 233000 / 110000,
            "financing_intensity": 110000 / 1200000, "nc_asset_weight": 251000 / 557000,
        })

    def test_profitability_dupont_and_leverage_2026(self):
        # FIN-08: ROE/ROA/ROI/ROIop/ROS/ROD; ROIop = ROS x turnover; ROE = [ROI+(ROI-ROD)D/E] x S x I
        self.assertRatios(self.analysis, 2026, {
            "roe": 116000 / 274000, "roe_avg": 116000 / 231000, "roa": 162000 / 557000, "roi": 164000 / 384000,
            "roi_op": 162000 / 277000, "ros": 0.135, "op_turnover": 1200000 / 277000, "rod": 8000 / 110000,
            "lev_s": 1.0, "lev_i": 116000 / 156000, "va_per_employee": 50300,
        })
        ratio = {code: self._ratio(self.analysis, code, 2026).value for code in ("roi_op", "ros", "op_turnover", "roe", "roe_leverage")}
        self.assertAlmostEqual(ratio["roi_op"], ratio["ros"] * ratio["op_turnover"], places=9)
        self.assertAlmostEqual(ratio["roe_leverage"], ratio["roe"], places=9)

    def test_ratio_without_previous_year_or_input_is_not_computable(self):
        # FIN-08 / FIN-11: missing prev year or typed input never yields a fake 0
        for code, year in (("roe_avg", 2024), ("wacc", 2025), ("va_per_employee", 2025)):
            row = self._ratio(self.analysis, code, year)
            self.assertEqual(row.status, "not_computable", "%s %s" % (code, year))
            self.assertEqual(row.value, 0.0)
        self.assertEqual(self._ratio(self.analysis, "roe", 2024).status, "ok")

    # ---- FIN-09 cash flow --------------------------------------------------------------------------

    def test_functional_cash_flow_reconciles_to_cash(self):
        # FIN-09: FCOC, FCO, FCFE by management area; delta cash reconciles with the balance sheet
        self.assertValues(self.analysis, 2026, {
            "cf_gross_self_financing": 209000, "cf_net_self_financing": 169000, "cf_fcoc": 162000,
            "cf_op_investment": 55000, "cf_fco": 107000, "cf_ordinary": 104000, "cf_debt_service": 104000,
            "cf_fcfe": 66000, "cf_cash_change": 36000, "cf_check": 0,
        })
        # 2025 reads 2024 from the history import as previous year
        self.assertValues(self.analysis, 2025, {"cf_fcoc": 134000, "cf_fco": 84000, "cf_fcfe": 46000, "cf_cash_change": 26000, "cf_check": 0})
        self.assertFalse(self.analysis.value_ids.filtered(lambda row: row.code == "cf_fco" and row.year == 2024))

    def test_oic10_indirect_cash_flow_reconciles_to_cash(self):
        # FIN-09: OIC 10 indirect A (operating) + B (investing) + C (financing) = delta cash
        self.assertValues(self.analysis, 2026, {
            "oic_a1": 162000, "oic_a2": 233000, "oic_a3": 221000, "oic_a": 156000, "oic_b": -60000,
            "oic_c": -60000, "oic_delta": 36000, "oic_check": 0,
        })
        self.assertValues(self.analysis, 2025, {"oic_a": 126000, "oic_b": -50000, "oic_c": -50000, "oic_delta": 26000, "oic_check": 0})
        self.assertRatios(self.analysis, 2026, {"st_debt_coverage": 187000 / 20000})

    # ---- FIN-10 crisis -----------------------------------------------------------------------------

    def test_crisis_indicators_with_thresholds(self):
        # FIN-10: equity, economic and financial area; autonomy < 50% raises the alert
        statuses = {code: self._ratio(self.analysis, code, 2026) for code in (
            "crisis_balance", "crisis_ccnop", "crisis_autonomy", "crisis_mon", "crisis_ebit_charges",
            "crisis_spread", "crisis_fco_debt",
        )}
        self.assertAlmostEqual(statuses["crisis_balance"].value, 163000, places=2)
        self.assertAlmostEqual(statuses["crisis_fco_debt"].value, 79000, places=2)
        self.assertEqual({code: row.status for code, row in statuses.items()}, {
            "crisis_balance": "ok", "crisis_ccnop": "ok", "crisis_autonomy": "alert", "crisis_mon": "ok",
            "crisis_ebit_charges": "ok", "crisis_spread": "ok", "crisis_fco_debt": "ok",
        })
        self.assertEqual(self._ratio(self.analysis, "crisis_fco_debt", 2024).status, "not_computable")

    # ---- FIN-11 value ------------------------------------------------------------------------------

    def test_wacc_and_eva_from_typed_inputs(self):
        # FIN-11: Ke = rf + beta (rm - rf); WACC on E = PN, D = PFN, Kd = ROD; EVA = NOPAT - WACC x COIN
        rod = 8000 / 110000
        wacc = 0.09 * 274000 / 277000 + rod * (1 - 0.24) * 3000 / 277000
        self.assertRatios(self.analysis, 2026, {"ke": 0.09, "wacc": wacc, "nopat": 123120})
        self.assertRatios(self.analysis, 2026, {"eva": 123120 - wacc * 277000}, places=2)

    # ---- FIN-02 overrides / reduced mode -----------------------------------------------------------

    def test_overrides_reclassify_year_end_balances(self):
        # FIN-02: short-term portion of the loan, receivables beyond 12 months, shareholder loan as equity
        bs = self.schemes["scheme_bs_functional"]
        common = {"company_id": self.company.id, "scheme_id": bs.id, "year": 2026}
        self.env["cmc.fin.override"].create([
            {**common, "account_id": self.acc["FXMUT"].id, "line_id": self._line(bs, "fin_debt_c").id,
             "amount": 20000, "reason": "Principal due in 2027 per amortisation plan"},
            {**common, "account_id": self.acc["FXCLI"].id, "line_id": self._line(bs, "other_op_nc").id,
             "amount": 15000, "reason": "Receivables due after 12 months (notes)"},
            {**common, "account_id": self.acc["FXSOC"].id, "line_id": self._line(bs, "equity").id,
             "reason": "Interest-free subordinated shareholder loan"},
        ])
        run = self._run(2025, 2026)
        run.action_compute()
        self.assertValues(run, 2026, {
            "fin_debt_nc": 40000, "fin_debt_c": 40000, "receivables": 135000, "other_op_nc": 15000,
            "equity": 304000, "op_assets_nc": 241000, "ccnop": 86000, "coin": 277000, "pfn": -27000,
            "liab_nc": 90000, "liab_c": 163000, "total_assets": 557000, "total_sources": 557000,
        })
        # the choices are per year: 2025 is untouched
        self.assertValues(run, 2025, {"fin_debt_c": 30000, "equity": 188000, "receivables": 132000})
        self.assertEqual(self._ratio(run, "crisis_autonomy", 2026).status, "ok")

    def test_reduced_mode_flags_detail_ratios(self):
        # FIN-02 / abbreviated statements: detail ratios are flagged, the minimum set is computed
        run = self._run(2026, 2026, reduced_mode=True)
        run.action_compute()
        for code in ("dio_raw", "dio_finished", "commercial_cycle"):
            self.assertEqual(self._ratio(run, code, 2026).status, "not_computable", code)
        self.assertRatios(run, 2026, {"roe": 116000 / 274000, "dso": 45.0})

    # ---- integrity and lifecycle -------------------------------------------------------------------

    def test_unmapped_account_blocks_compute(self):
        # FIN-02: an account no rule places would unbalance the statement; the user is told which one
        bare = self.env["cmc.fin.scheme"].create({
            "name": "Bare", "code": "bare_bs", "scheme_type": "balance_sheet", "company_id": self.company.id,
            "line_ids": [Command.create({"code": "bare_total", "name": "Total", "line_type": "account", "sign": 1})],
        })
        run = self._run(2026, 2026, scheme_ids=[Command.set(bare.ids)])
        with self.assertRaises(UserError) as caught:
            run.action_compute()
        self.assertIn("FXCLI", str(caught.exception))

    def test_run_lifecycle_locked_and_reset(self):
        # AD-001 / AD-113: computed snapshot is locked; reset deletes the results
        run = self._run(2026, 2026)
        run.action_compute()
        self.assertEqual(run.state, "done")
        self.assertTrue(run.value_ids and run.ratio_value_ids)
        with self.assertRaises(UserError):
            run.write({"reduced_mode": True})
        with self.assertRaises(UserError):
            run.value_ids[:1].write({"value": 1.0})
        run.action_reset_to_draft()
        self.assertEqual(run.state, "draft")
        self.assertFalse(run.value_ids or run.ratio_value_ids)

    def test_snapshot_not_changed_by_later_postings(self):
        # AD-116: results are snapshots; a late posting shows only after reset + compute
        run = self._run(2026, 2026)
        run.action_compute()
        self._entry(date(2026, 12, 31), {"FXRIC": -5000, "FXCLI": 5000})
        self.assertAlmostEqual(self._val(run, "revenue", 2026), 1200000, places=2)
        run.action_reset_to_draft()
        run.action_compute()
        self.assertAlmostEqual(self._val(run, "revenue", 2026), 1205000, places=2)

    def test_compute_query_count_independent_of_journal_items(self):
        # AD-114: the GL is aggregated; 200 more journal items must not add a single query
        self._run(2026, 2026).action_compute()  # warm-up (registry / ormcache)

        def queries(run):
            self.env.flush_all()
            self.env.invalidate_all()
            before = self.env.cr.sql_log_count
            run.action_compute()
            return self.env.cr.sql_log_count - before

        small = queries(self._run(2026, 2026))
        move = self.env["account.move"].create({
            "move_type": "entry", "date": date(2026, 9, 30), "journal_id": self.company_data["default_journal_misc"].id,
            "line_ids": [Command.create({"name": "vol", "account_id": self.acc["FXSER"].id, "debit": 10.0}) for _i in range(100)]
            + [Command.create({"name": "vol", "account_id": self.acc["FXRIC"].id, "credit": 10.0}) for _i in range(100)],
        })
        move.action_post()
        big = queries(self._run(2026, 2026))
        self.assertEqual(big, small)
