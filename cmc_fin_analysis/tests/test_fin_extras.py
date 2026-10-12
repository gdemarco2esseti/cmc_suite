"""WP-13 extras: typed-input uniqueness, what-if cycle (FIN-06), liquidity dynamics (FIN-09),
data-quality red flags (FIN-10), MVA / EV / equity value (FIN-11), valuation-criteria changes (FIN-05).

CONTRACT: see ``tests/common.py`` (section "Extras"). Expected figures are hand-computed from the 3-year
fixture there. Shared runs are computed by the fixture user (role coverage of compute lives in
test_fin_security); the tests naming a role act AS the CMC user (``with_user``).
"""

import psycopg2

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged
from odoo.tools import mute_logger

from .common import FinAnalysisCommon

# what-if targets typed for 2026 (days)
TARGETS_2026 = {"target_dso": 30, "target_dio_raw": 18, "target_dio_finished": 9, "target_dpo": 36}

# 2026 WACC of the fixture: Ke 9%, E = PN 274,000, D = PFN 3,000, Kd = ROD 8,000/110,000, t 24%
WACC_2026 = 0.09 * 274000 / 277000 + (8000 / 110000) * (1 - 0.24) * 3000 / 277000
NOPAT_2026 = 123120  # MON 162,000 x (1 - 0.24)


@tagged("post_install", "-at_install")
class TestFinExtras(FinAnalysisCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["cmc.fin.input"].create([
            {"company_id": cls.company.id, "year": 2026, "code": code, "value": value}
            for code, value in TARGETS_2026.items()
        ])
        cls.analysis = cls._run(2024, 2026)
        cls.analysis.action_compute()

    # ---- typed inputs ------------------------------------------------------------------------------

    def test_input_unique_per_company_year_code(self):
        # AD-116: one typed value per (company, year, code), otherwise the namespace would be ambiguous
        Input = self.env["cmc.fin.input"]
        Input.create({"company_id": self.company.id, "year": 2023, "code": "rf", "value": 0.02})
        # same code, other year / other company: allowed
        Input.create({"company_id": self.company.id, "year": 2022, "code": "rf", "value": 0.02})
        Input.create({"company_id": self.company_b.id, "year": 2023, "code": "rf", "value": 0.02})
        with self.assertRaises(psycopg2.IntegrityError), mute_logger("odoo.sql_db"), self.env.cr.savepoint():
            Input.create({"company_id": self.company.id, "year": 2023, "code": "rf", "value": 0.05})
            self.env.flush_all()

    def test_input_code_clashing_with_line_code_blocks_compute(self):
        # AD-116: an input named like a line is "a code defined twice" -> the user is told which one
        self.env["cmc.fin.input"].create({"company_id": self.company.id, "year": 2026, "code": "revenue", "value": 1.0})
        run = self._run(2026, 2026)
        with self.assertRaises(UserError) as caught:
            run.action_compute()
        self.assertIn("revenue", str(caught.exception))

    # ---- FIN-06 what-if on the commercial cycle ----------------------------------------------------

    def test_whatif_ratios_shipped(self):
        # FIN-06: what-if ratios ship as shared liquidity ratios, detail only (need inventory split)
        for code, unit in (("whatif_cycle", "days"), ("whatif_cash_release", "amount")):
            ratio = self._ratio_def(code)
            self.assertTrue(ratio, code)
            self.assertRecordValues(ratio, [{"area": "liquidity", "unit": unit, "full_only": True}])

    def test_whatif_cycle_and_cash_release_2026(self):
        # FIN-06: target days -> target cycle; cash released = current working capital - target working capital
        # receivables 150,000 - 30 x 1,200,000/360 = 50,000; raw 33,000 - 18 x 483,000/360 = 8,850;
        # finished 30,000 - 9 x 818,000/360 = 9,550; payables 95,000 - 36 x 700,000/360 = 25,000 (absorbed)
        self.assertRatios(self.analysis, 2026, {"whatif_cycle": 21.0})
        self.assertRatios(self.analysis, 2026, {"whatif_cash_release": 43400.0}, places=2)

    def test_whatif_without_targets_is_not_computable(self):
        # FIN-06: no typed target for the year -> not computable, never a fake 0
        for code in ("whatif_cycle", "whatif_cash_release"):
            row = self._ratio(self.analysis, code, 2025)
            self.assertEqual(row.status, "not_computable", code)
            self.assertEqual(row.value, 0.0, code)

    def test_whatif_reduced_mode_not_computable(self):
        # FIN-06 / FIN-02: abbreviated statements have no inventory split -> what-if flagged
        run = self._run(2026, 2026, user=self.cmc_user, reduced_mode=True)
        run.action_compute()
        for code in ("whatif_cycle", "whatif_cash_release"):
            self.assertEqual(self._ratio(run, code, 2026).status, "not_computable", code)

    # ---- FIN-09 liquidity dynamics -----------------------------------------------------------------

    def test_liquidity_scheme_shipped_formula_only(self):
        # FIN-09 / AD-116: the liquidity table derives from BS deltas + P&L of the same run
        scheme = self.env.ref("cmc_fin_analysis.scheme_cf_liquidity")
        oic = self.schemes["scheme_cf_oic10"]
        self.assertEqual(scheme.scheme_type, "cash_flow")
        self.assertFalse(scheme.company_id)
        self.assertFalse(scheme.base_code)
        self.assertGreater(scheme.sequence, oic.sequence)
        self.assertEqual(set(scheme.line_ids.mapped("line_type")), {"formula"})
        self.assertLessEqual(
            {"liq_generated", "liq_abs_op_c", "liq_abs_op_nc", "liq_abs_debt", "liq_abs_equity", "liq_residual", "liq_residual_pct"},
            set(scheme.line_ids.mapped("code")),
        )
        self.assertIn(scheme, self.analysis.scheme_ids)

    def test_liquidity_dynamics_2026(self):
        # FIN-09 (Fazzini 7.8): generated 211,000; absorbed by op. current 47,000, op. non-current 60,000,
        # debt 38,000, equity 30,000 -> residual 36,000 = 17.06% of generated = cash change
        self.assertValues(self.analysis, 2026, {
            "liq_generated": 211000, "liq_abs_op_c": 47000, "liq_abs_op_nc": 60000, "liq_abs_debt": 38000,
            "liq_abs_equity": 30000, "liq_residual": 36000,
        })
        self.assertAlmostEqual(self._val(self.analysis, "liq_residual_pct", 2026), 36000 / 211000 * 100, places=4)

    def test_liquidity_residual_equals_cash_change_every_year(self):
        # FIN-09: the liquidity table closes on the functional cash flow (2025 reads 2024 from history)
        self.assertValues(self.analysis, 2025, {
            "liq_generated": 169000, "liq_abs_op_c": 34000, "liq_abs_op_nc": 50000, "liq_abs_debt": 39000,
            "liq_abs_equity": 20000, "liq_residual": 26000,
        })
        for year in (2025, 2026):
            self.assertAlmostEqual(self._val(self.analysis, "liq_residual", year), self._val(self.analysis, "cf_cash_change", year), places=2)
        # first run year: no previous balance sheet -> no residual row (NameError on prev_*)
        self.assertFalse(self.analysis.value_ids.filtered(lambda row: row.code == "liq_residual" and row.year == 2024))
        self.assertValues(self.analysis, 2024, {"liq_generated": 131000})

    # ---- FIN-10 data-quality red flags -------------------------------------------------------------

    def test_data_quality_area_and_ratios_shipped(self):
        # FIN-10: red flags are ratios of their own area, alert when the growth gap is positive
        area = dict(self.env["cmc.fin.ratio"]._fields["area"]._description_selection(self.env))
        self.assertEqual(area.get("data_quality"), "Data quality")
        for code, unit in (("dq_intangible_growth", "amount"), ("dq_inventory_vs_revenue", "ratio"), ("dq_receivables_vs_revenue", "ratio")):
            ratio = self._ratio_def(code)
            self.assertTrue(ratio, code)
            self.assertRecordValues(ratio, [{"area": "data_quality", "unit": unit, "threshold_op": "<=", "threshold": 0.0}])

    def test_data_quality_flags_2025_2026(self):
        # FIN-10: inventory / receivables growing faster than revenue, intangibles growing -> alert
        expected = {
            2025: {"dq_intangible_growth": (-2000, "ok"), "dq_inventory_vs_revenue": (60000 / 50000 - 1.1, "alert"),
                   "dq_receivables_vs_revenue": (0.0, "ok")},
            2026: {"dq_intangible_growth": (-2000, "ok"), "dq_inventory_vs_revenue": (63000 / 60000 - 1200000 / 1100000, "ok"),
                   "dq_receivables_vs_revenue": (150000 / 132000 - 1200000 / 1100000, "alert")},
        }
        for year, ratios in expected.items():
            for code, (value, status) in ratios.items():
                row = self._ratio(self.analysis, code, year)
                self.assertAlmostEqual(row.value, value, places=6, msg="%s %s" % (code, year))
                self.assertEqual(row.status, status, "%s %s" % (code, year))
        for code in expected[2026]:
            self.assertEqual(self._ratio(self.analysis, code, 2024).status, "not_computable", code)

    # ---- FIN-11 MVA / EV / equity value ------------------------------------------------------------

    def test_value_mva_ev_equity_value_2026(self):
        # FIN-11 (Fazzini 9.2): COV = current EVA / WACC; EV = COIN + MVA; We = EV + CEIN(0) - PFN
        eva = NOPAT_2026 - WACC_2026 * 277000
        mva = eva / WACC_2026
        self.assertRatios(self.analysis, 2026, {"mva_cov": mva, "ev": 277000 + mva, "equity_value": 277000 + mva - 3000}, places=2)
        # EV collapses to NOPAT / WACC (perpetuity of current NOPAT)
        self.assertAlmostEqual(self._ratio(self.analysis, "ev", 2026).value, NOPAT_2026 / WACC_2026, places=2)
        for code in ("mva_cov", "ev", "equity_value"):
            self.assertRecordValues(self._ratio_def(code), [{"area": "value", "unit": "amount"}])
            self.assertEqual(self._ratio(self.analysis, code, 2025).status, "not_computable", code)


@tagged("post_install", "-at_install")
class TestFinCriteriaChange(FinAnalysisCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # FIN-05: LIFO -> FIFO on finished goods from 2025 (registered by the CMC user who owns the analysis)
        cls.change = cls.env["cmc.fin.criteria.change"].with_user(cls.cmc_user).create({
            "company_id": cls.company.id, "year": 2025, "name": "Finished goods LIFO -> FIFO",
            "reason": "Inventory +4,000 vs LIFO; COIN and PN restated", "line_codes": "inv_finished, inventory,coin",
        })
        # another company's change must not leak into company A
        cls.env["cmc.fin.criteria.change"].with_company(cls.company_b).create({
            "company_id": cls.company_b.id, "year": 2026, "name": "Receivables at fair value", "line_codes": "receivables",
        })
        cls.analysis = cls._run(2024, 2026)
        cls.analysis.action_compute()

    def test_criteria_change_flags_lines_of_that_year(self):
        # FIN-05: the year of a valuation-criteria change and its YoY are not comparable for the lines hit
        for code in ("inv_finished", "inventory", "coin"):
            self.assertTrue(self._row(self.analysis, code, 2025).comparability_break, code)
            for year in (2024, 2026):
                self.assertFalse(self._row(self.analysis, code, year).comparability_break, "%s %s" % (code, year))
        for code in ("inv_raw", "receivables", "revenue", "pfn"):
            self.assertFalse(self._row(self.analysis, code, 2025).comparability_break, code)

    def test_criteria_change_of_other_company_ignored(self):
        # AD-115: a change of company B never marks company A rows
        self.assertFalse(self.analysis.value_ids.filtered(lambda row: row.code == "receivables").filtered("comparability_break"))
        self.assertEqual(len(self.analysis.value_ids.filtered("comparability_break")), 3)

    def test_criteria_change_requires_existing_line_codes(self):
        # FIN-05: a typo in the codes would silently flag nothing
        with self.assertRaises(ValidationError):
            self.env["cmc.fin.criteria.change"].create({
                "company_id": self.company.id, "year": 2026, "name": "Typo", "line_codes": "inventroy",
            })

    def test_criteria_change_register_by_role(self):
        # AD-115: the CMC user maintains the register; a user outside CMC cannot read nor write it
        self.assertEqual(self.change.create_uid, self.cmc_user)
        self.change.with_user(self.cmc_user).write({"reason": "Restated in notes"})
        with self.assertRaises(AccessError):
            self.env["cmc.fin.criteria.change"].with_user(self.outsider).search([])
        with self.assertRaises(AccessError):
            self.env["cmc.fin.criteria.change"].with_user(self.outsider).create({
                "company_id": self.company.id, "year": 2026, "name": "No", "line_codes": "coin",
            })
        self.assertFalse(self.env["cmc.fin.criteria.change"].with_user(self.cmc_user_b).search([("id", "=", self.change.id)]))

    def test_criteria_change_reset_and_recompute(self):
        # FIN-05 / AD-116: the flag is part of the snapshot; removing the change shows after reset + compute
        run = self._run(2025, 2025)
        run.action_compute()
        self.assertTrue(self._row(run, "coin", 2025).comparability_break)
        self.change.unlink()
        run.action_reset_to_draft()
        run.action_compute()
        self.assertFalse(run.value_ids.filtered("comparability_break"))
