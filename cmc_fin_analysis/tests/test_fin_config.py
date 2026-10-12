"""WP-13 configuration: shipped schemes / rules / ratios and their validations (FIN-01..FIN-11).

CONTRACT: see ``tests/common.py`` (models, fields, shipped xmlids, line and ratio codes, formulas).
This file pins the shipped data and the input validations; no GL is read here.
"""

import psycopg2

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger

from .common import SCHEME_XMLIDS

BS_ACCOUNT_LINES = {
    "intangible": 1, "tangible": 1, "other_op_nc": 1, "fin_assets_nc": 1, "inv_raw": 1, "inv_finished": 1,
    "receivables": 1, "other_op_c": 1, "fin_assets_c": 1, "cash": 1,
    "equity": -1, "op_liab_nc": -1, "fin_debt_nc": -1, "payables": -1, "other_op_liab_c": -1, "fin_debt_c": -1,
}
BS_FORMULA_LINES = {
    "op_assets_nc", "inventory", "op_assets_c", "assets_nc", "assets_c", "total_assets", "op_liab_c", "liab_nc",
    "liab_c", "total_sources", "net_fixed", "ccnop", "coin", "fin_assets", "cin", "fin_debt", "pfn", "pfn_nc", "pfn_c",
}
VA_ACCOUNT_LINES = {
    "revenue": -1, "fg_inv_change": -1, "other_revenue": -1, "fin_income": -1, "extraordinary": -1,
    "purchases": 1, "raw_inv_change": 1, "services": 1, "other_expenses": 1, "personnel": 1, "severance": 1,
    "depreciation": 1, "provisions": 1, "fin_charges": 1, "taxes": 1,
}
VA_FORMULA_LINES = {
    "production_value", "external_costs", "value_added", "ebitda", "non_cash", "mon", "ebit", "ordinary_result",
    "pre_tax", "net_income", "raw_consumption", "gross_cash_flow",
}
RATIOS = {
    "liquidity": {"treasury_margin", "quick_ratio", "nwc", "current_ratio", "dso", "dpo", "dio_raw", "dio_finished",
                  "commercial_cycle", "cash_coverage"},
    "solidity": {"autonomy", "debt_equity", "structure_margin", "fixed_asset_coverage", "interest_coverage",
                 "debt_coverage", "financing_intensity", "nc_asset_weight"},
    "profitability": {"roe", "roe_avg", "roa", "roi", "roi_op", "ros", "op_turnover", "rod", "lev_s", "lev_i",
                      "roe_leverage", "va_per_employee"},
    "cash_flow": {"st_debt_coverage"},
    "value": {"ke", "wacc", "nopat", "eva"},
    "crisis": {"crisis_balance", "crisis_ccnop", "crisis_autonomy", "crisis_mon", "crisis_ebit_charges",
               "crisis_spread", "crisis_fco_debt"},
}


@tagged("post_install", "-at_install")
class TestFinConfig(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.schemes = {xid: cls.env.ref("cmc_fin_analysis.%s" % xid) for xid in SCHEME_XMLIDS}
        cls.bs = cls.schemes["scheme_bs_functional"]
        cls.va = cls.schemes["scheme_pl_value_added"]
        cls.company = cls.env.company

    def _codes(self, scheme, line_type=None):
        lines = scheme.line_ids.filtered(lambda line: not line_type or line.line_type == line_type)
        return {line.code: line for line in lines}

    # ---- shipped data -----------------------------------------------------------------------

    def test_shipped_schemes_types_and_base(self):
        # AD-116 / FIN-01,03,04,09: Italian default schemes ship as shared data
        expected = {
            "scheme_bs_functional": ("balance_sheet", "total_assets"),
            "scheme_pl_value_added": ("income_statement", "revenue"),
            "scheme_pl_cost_of_sales": ("income_statement", "cs_revenue"),
            "scheme_pl_contribution": ("income_statement", "cm_revenue"),
            "scheme_cf_functional": ("cash_flow", False),
            "scheme_cf_oic10": ("cash_flow", False),
        }
        for xid, (scheme_type, base) in expected.items():
            scheme = self.schemes[xid]
            self.assertEqual(scheme.scheme_type, scheme_type, xid)
            self.assertEqual(scheme.base_code or False, base, xid)
            self.assertFalse(scheme.company_id, xid)

    def test_shipped_bs_lines_signs_and_result_line(self):
        # FIN-01: one scheme holds the financial (current/non-current) and the functional aggregates
        accounts = self._codes(self.bs, "account")
        self.assertEqual({code: line.sign for code, line in accounts.items()}, BS_ACCOUNT_LINES)
        self.assertLessEqual(BS_FORMULA_LINES, set(self._codes(self.bs, "formula")))
        self.assertEqual(self.bs.line_ids.filtered("result_line").mapped("code"), ["equity"])

    def test_shipped_value_added_lines(self):
        # FIN-03: value-added P&L, costs and revenues in presentation sign
        accounts = self._codes(self.va, "account")
        self.assertEqual({code: line.sign for code, line in accounts.items()}, VA_ACCOUNT_LINES)
        self.assertLessEqual(VA_FORMULA_LINES, set(self._codes(self.va, "formula")))

    def test_shipped_cost_of_sales_and_contribution_lines(self):
        # FIN-04: cost-of-sales and contribution-margin P&L at company level
        cs = self._codes(self.schemes["scheme_pl_cost_of_sales"])
        self.assertLessEqual({"cs_revenue", "cs_cogs", "cs_overheads", "cs_other", "cs_industrial_margin",
                              "cs_operating_result", "cs_net_income"}, set(cs))
        cm = self._codes(self.schemes["scheme_pl_contribution"])
        self.assertLessEqual({"cm_revenue", "cm_variable", "cm_fixed", "cm_other", "cm_margin",
                              "cm_operating_result", "cm_net_income"}, set(cm))

    def test_shipped_cash_flow_schemes_are_formula_only(self):
        # FIN-09: both statements derive from BS deltas + P&L of the same run, never from accounts
        for xid, codes in (
            ("scheme_cf_functional", {"cf_fcoc", "cf_fco", "cf_fcfe", "cf_cash_change", "cf_check"}),
            ("scheme_cf_oic10", {"oic_a", "oic_b", "oic_c", "oic_delta", "oic_check"}),
        ):
            scheme = self.schemes[xid]
            self.assertLessEqual(codes, set(self._codes(scheme)), xid)
            self.assertEqual(set(scheme.line_ids.mapped("line_type")), {"formula"}, xid)

    def test_shipped_default_rules_by_account_type(self):
        # FIN-02: the shared defaults place every Odoo account type, company rules refine them
        defaults = {
            rule.account_type: rule.line_id.code
            for rule in self.bs.rule_ids.filtered(lambda r: not r.company_id and r.account_type)
        }
        self.assertEqual(defaults, {
            "asset_fixed": "tangible", "asset_non_current": "other_op_nc", "asset_receivable": "receivables",
            "asset_current": "other_op_c", "asset_prepayments": "other_op_c", "asset_cash": "cash",
            "liability_payable": "payables", "liability_current": "other_op_liab_c",
            "liability_credit_card": "fin_debt_c", "liability_non_current": "fin_debt_nc",
            "equity": "equity", "equity_unaffected": "equity",
        })
        va_defaults = {
            rule.account_type: rule.line_id.code
            for rule in self.va.rule_ids.filtered(lambda r: not r.company_id and r.account_type)
        }
        self.assertEqual(va_defaults, {
            "income": "revenue", "income_other": "other_revenue", "expense_direct_cost": "purchases",
            "expense": "services", "expense_depreciation": "depreciation",
        })
        # chart-agnostic type rules come after company rules (10) and chart-specific prefix rules (11..99)
        self.assertTrue(all(rule.sequence >= 100 for rule in self.bs.rule_ids | self.va.rule_ids
                            if not rule.company_id and not rule.chart_template))

    def test_shipped_contribution_rules_use_behavior_first(self):
        # AD-116: contribution P&L reads the account behavior of the cmc_account classification rules
        scheme = self.schemes["scheme_pl_contribution"]
        behavior = {r.cmc_behavior: (r.line_id.code, r.sequence) for r in scheme.rule_ids if r.cmc_behavior and not r.company_id}
        self.assertEqual({k: v[0] for k, v in behavior.items()},
                         {"variable": "cm_variable", "fixed": "cm_fixed", "semi_variable": "cm_fixed"})
        type_seqs = [r.sequence for r in scheme.rule_ids if r.account_type and not r.company_id]
        self.assertLess(max(v[1] for v in behavior.values()), min(type_seqs))

    def test_shipped_ratios_by_area(self):
        # FIN-06..FIN-11: the ratio pack ships as shared expressions
        ratios = self.env["cmc.fin.ratio"].search([("company_id", "=", False)])
        by_area = {}
        for ratio in ratios:
            by_area.setdefault(ratio.area, set()).add(ratio.code)
        for area, codes in RATIOS.items():
            self.assertLessEqual(codes, by_area.get(area, set()), area)
        full_only = set(ratios.filtered("full_only").mapped("code"))
        self.assertEqual(full_only & {"dio_raw", "dio_finished", "commercial_cycle", "roe"},
                         {"dio_raw", "dio_finished", "commercial_cycle"})

    def test_shipped_crisis_thresholds(self):
        # FIN-10: crisis indicators are ratios with thresholds (Fazzini tav. 13.1-13.3)
        crisis = self.env["cmc.fin.ratio"].search([("company_id", "=", False), ("area", "=", "crisis")])
        thresholds = {r.code: (r.threshold_op, r.threshold) for r in crisis}
        for code in RATIOS["crisis"] - {"crisis_autonomy"}:
            self.assertEqual(thresholds[code], (">", 0.0), code)
        self.assertEqual(thresholds["crisis_autonomy"], (">=", 0.5))

    # ---- validations ------------------------------------------------------------------------

    def test_line_code_must_be_identifier(self):
        # safe_eval namespace: a code is a Python identifier
        with self.assertRaises(ValidationError):
            self.env["cmc.fin.scheme.line"].create({
                "scheme_id": self.bs.id, "code": "total assets", "name": "x", "line_type": "formula", "formula": "1",
            })

    def test_formula_rejects_unsafe_or_invalid_expression(self):
        # AD-116: formulas are core safe_eval expressions, checked when saved
        Line = self.env["cmc.fin.scheme.line"]
        for formula in ("__import__('os').getcwd()", "coin +", "coin.__class__"):
            with self.assertRaises(ValidationError, msg=formula):
                Line.create({"scheme_id": self.bs.id, "code": "bad_x", "name": "x", "line_type": "formula", "formula": formula})

    def test_ratio_expression_rejects_unsafe_expression(self):
        # AD-116: same safety for ratio expressions
        with self.assertRaises(ValidationError):
            self.env["cmc.fin.ratio"].create({
                "code": "evil", "name": "Evil", "area": "value", "expression": "__import__('os').system('true')",
                "company_id": self.company.id,
            })

    def test_mapping_rule_needs_a_criterion(self):
        # FIN-02: a rule without criteria would swallow every account
        with self.assertRaises(ValidationError):
            self.env["cmc.fin.mapping.rule"].create({
                "scheme_id": self.bs.id, "company_id": self.company.id, "line_id": self.bs.line_ids.filtered(lambda l: l.code == "cash").id,
            })

    def test_mapping_rule_line_must_be_account_line_of_scheme(self):
        # FIN-02: a rule feeds an account line of its own scheme only
        Rule = self.env["cmc.fin.mapping.rule"]
        revenue = self.va.line_ids.filtered(lambda l: l.code == "revenue")
        with self.assertRaises(ValidationError):
            Rule.create({"scheme_id": self.bs.id, "company_id": self.company.id, "account_code_prefix": "9", "line_id": revenue.id})
        coin = self.bs.line_ids.filtered(lambda l: l.code == "coin")
        with self.assertRaises(ValidationError):
            Rule.create({"scheme_id": self.bs.id, "company_id": self.company.id, "account_code_prefix": "9", "line_id": coin.id})

    def test_override_requires_reason(self):
        # FIN-02 choices register: every discretionary reclassification is justified
        account = self.env["account.account"].search([("company_ids", "in", self.company.ids)], limit=1)
        target = self.bs.line_ids.filtered(lambda l: l.code == "fin_debt_c")
        with self.assertRaises(ValidationError):
            self.env["cmc.fin.override"].create({
                "company_id": self.company.id, "scheme_id": self.bs.id, "year": 2026,
                "account_id": account.id, "line_id": target.id, "amount": 100.0, "reason": "   ",
            })

    def test_override_target_must_be_account_line_of_scheme(self):
        # FIN-02: an override moves a balance between account lines of the same scheme
        account = self.env["account.account"].search([("company_ids", "in", self.company.ids)], limit=1)
        coin = self.bs.line_ids.filtered(lambda l: l.code == "coin")
        with self.assertRaises(ValidationError):
            self.env["cmc.fin.override"].create({
                "company_id": self.company.id, "scheme_id": self.bs.id, "year": 2026,
                "account_id": account.id, "line_id": coin.id, "reason": "test",
            })

    def test_history_unique_per_company_year_account(self):
        # AD-116: one imported balance per account and year
        account = self.env["account.account"].search([("company_ids", "in", self.company.ids)], limit=1)
        History = self.env["cmc.fin.history"]
        History.create({"company_id": self.company.id, "year": 2023, "account_id": account.id, "balance": 10.0})
        with self.assertRaises(psycopg2.IntegrityError), mute_logger("odoo.sql_db"), self.env.cr.savepoint():
            History.create({"company_id": self.company.id, "year": 2023, "account_id": account.id, "balance": 5.0})
            self.env.flush_all()

    def test_run_year_span_validation(self):
        # AD-113: a run covers an ordered span of fiscal years
        with self.assertRaises(ValidationError):
            self.env["cmc.fin.analysis"].create({
                "name": "Backwards", "company_id": self.company.id, "year_from": 2026, "year_to": 2024,
            })
