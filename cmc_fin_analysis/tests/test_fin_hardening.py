"""WP-13 ronin hardening: Italian chart defaults (RN-001), excluded journals (RN-002), strict
formula/ratio names (RN-003).

CONTRACT: see ``tests/common.py`` (section "Hardening"). Expected figures are hand-computed below.
"""

import unittest
from datetime import date

from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import MOVES_2026, FinAnalysisCommon

# RN-001 Italian company, fiscal year 2026 (natural sign, debit +), accounts of l10n_it account.account-it.csv.
# The chart pads the codes to 6 digits (2101 -> 210100); "BANK" = the asset_cash account of the chart (182001).
IT_ENTRIES = [
    ("2026-01-02", {"BANK": 100000, "2101": -100000}),  # share capital
    ("2026-01-10", {"BANK": 350000, "2410": -350000}),  # 10-year mortgage
    ("2026-01-15", {"1201": 400000, "BANK": -400000}),  # building
    ("2026-03-31", {"4101": 50000, "BANK": -50000}),  # goods purchased
    ("2026-06-30", {"BANK": 270000, "1501": 30000, "3101": -300000}),  # sales
    ("2026-07-31", {"4401": 60000, "4402": 20000, "4403": 5000, "BANK": -80000, "2301": -5000}),  # payroll + TFR
    ("2026-09-30", {"5210": 6000, "BANK": -6000}),  # mortgage interest
    ("2026-09-30", {"5115": -1000, "BANK": 1000}),  # bank interest income
    ("2026-10-31", {"BANK": 25000, "2411": -25000}),  # short-term bank credit
    ("2026-12-31", {"4601": 10000, "1211": -10000}),  # building depreciation
    ("2026-12-31", {"4706": 2000, "1540": -2000}),  # bad-debt allowance, netted in receivables
    ("2026-12-31", {"4814": 3000, "2204": -3000}),  # provision for liabilities
    ("2026-12-31", {"1401": 5000, "1404": 15000, "4131": -20000}),  # closing inventory
    ("2026-12-31", {"8101": 4000, "2606": -4000}),  # income taxes
]
# hand-computed 2026 presentation values
IT_EXPECTED = {
    "intangible": 0, "tangible": 390000, "fin_assets_nc": 0, "inv_raw": 5000, "inv_finished": 15000,
    "inventory": 20000, "receivables": 28000, "other_op_c": 0, "cash": 210000, "total_assets": 648000,
    "equity": 261000, "op_liab_nc": 5000, "fin_debt_nc": 350000, "fin_debt_c": 25000, "fin_debt": 375000,
    "liab_nc": 355000, "total_sources": 648000,
    "revenue": 300000, "purchases": 50000, "raw_inv_change": -20000, "services": 0, "personnel": 80000,
    "severance": 5000, "depreciation": 12000, "provisions": 3000, "fin_income": 1000, "fin_charges": 6000,
    "taxes": 4000, "value_added": 270000, "ebitda": 190000, "mon": 170000, "net_income": 161000,
}


@tagged("post_install", "-at_install")
class TestFinChartTemplateRules(FinAnalysisCommon):
    """RN-001 parts that do not need l10n_it: shipped data shape and isolation of non-Italian companies."""

    def test_rn001_italian_prefix_rules_shipped(self):
        # RN-001: shared Italian rules, scoped to chart 'it', ranked after company rules and before type rules
        for xid in ("scheme_bs_functional", "scheme_pl_value_added"):
            rules = self.env["cmc.fin.mapping.rule"].search([("scheme_id", "=", self.schemes[xid].id), ("chart_template", "=", "it")])
            self.assertTrue(rules, xid)
            self.assertFalse(rules.company_id, xid)
            self.assertTrue(all(rule.account_code_prefix and 10 < rule.sequence < 100 for rule in rules), xid)

    def test_rn001_non_italian_company_ignores_italian_prefix_rules(self):
        # RN-001: an account coded like an Italian one on another chart keeps the account-type mapping
        Account = self.env["account.account"].with_company(self.company)
        wages = Account.create({"code": "4401NI", "name": "Wages lookalike", "account_type": "expense", "company_ids": [Command.set(self.company.ids)]})
        loan = Account.create({"code": "2410NI", "name": "Loan lookalike", "account_type": "liability_current", "company_ids": [Command.set(self.company.ids)]})
        self.assertNotEqual(self.company.chart_template, "it")
        move = self.env["account.move"].with_company(self.company).create({
            "move_type": "entry", "date": date(2026, 8, 31), "journal_id": self.company_data["default_journal_misc"].id,
            "line_ids": [
                Command.create({"name": "w", "account_id": wages.id, "debit": 7000.0}),
                Command.create({"name": "l", "account_id": loan.id, "credit": 7000.0}),
            ],
        })
        move.action_post()
        run = self._run(2026, 2026)
        run.action_compute()
        self.assertValues(run, 2026, {"personnel": 270000, "services": 227000, "fin_debt_nc": 90000, "other_op_liab_c": 35000})


@tagged("post_install", "-at_install")
class TestFinItalianChart(FinAnalysisCommon):
    """RN-001: shipped defaults give a meaningful reclassification on the Italian chart, no manual rule."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # why: l10n_it is not a dependency of cmc_fin_analysis; the scenario only exists where it is installed
        if not cls.env["ir.module.module"].search_count([("name", "=", "l10n_it"), ("state", "=", "installed")]):
            raise unittest.SkipTest("l10n_it not installed: Italian chart scenario not covered")
        cls.it_company = cls.env["res.company"].create({
            "name": "FIN analysis IT", "country_id": cls.env.ref("base.it").id, "currency_id": cls.env.ref("base.EUR").id,
        })
        cls.env.user.company_ids = [Command.link(cls.it_company.id)]
        cls.env["account.chart.template"].try_loading("it", cls.it_company, install_demo=False)
        cls.it_env = cls.env(context=dict(cls.env.context, allowed_company_ids=[cls.it_company.id]))
        Journal = cls.it_env["account.journal"]
        cls.it_misc = Journal.search([("company_id", "=", cls.it_company.id), ("code", "=", "MISC")], limit=1)
        Account = cls.it_env["account.account"]
        bank = Account.search([("company_ids", "in", cls.it_company.id), ("account_type", "=", "asset_cash")], limit=1)
        for day, amounts in IT_ENTRIES:
            lines = []
            for code, amount in amounts.items():
                account = bank if code == "BANK" else Account.search([("code", "=", code.ljust(6, "0")), ("company_ids", "in", cls.it_company.id)], limit=1)
                assert account, "Italian chart account %s not found" % code
                lines.append(Command.create({
                    "name": code, "account_id": account.id,
                    "debit": amount if amount > 0 else 0.0, "credit": -amount if amount < 0 else 0.0,
                }))
            move = cls.it_env["account.move"].create({"move_type": "entry", "date": day, "journal_id": cls.it_misc.id, "line_ids": lines})
            move.action_post()

    def _it_run(self):
        run = self.it_env["cmc.fin.analysis"].create({"name": "IT 2026", "company_id": self.it_company.id, "year_from": 2026, "year_to": 2026})
        run.action_compute()
        return run

    def test_rn001_italian_chart_meaningful_without_manual_rules(self):
        # RN-001: mortgage = financial debt, 44xx personnel (4403 TFR), 46-47 depreciation, 52 charges, 14 inventory
        self.assertEqual(self.it_company.chart_template, "it")
        self.assertFalse(self.env["cmc.fin.mapping.rule"].search_count([("company_id", "=", self.it_company.id)]))
        self.assertValues(self._it_run(), 2026, IT_EXPECTED)

    def test_rn001_mortgage_gives_no_false_crisis_balance_alert(self):
        # RN-001: a 10-year mortgage financing the building is long-term: equity + liab_nc covers fixed assets
        row = self._ratio(self._it_run(), "crisis_balance", 2026)
        self.assertAlmostEqual(row.value, 226000, places=2)  # 261,000 + 355,000 - 390,000
        self.assertEqual(row.status, "ok")

    def test_rn001_company_rule_wins_over_italian_default(self):
        # RN-001: the shipped Italian rules are defaults; a company rule (sequence 10) still decides
        bs = self.schemes["scheme_bs_functional"]
        self.it_env["cmc.fin.mapping.rule"].create({
            "scheme_id": bs.id, "company_id": self.it_company.id, "sequence": 10,
            "account_code_prefix": "2410", "line_id": self._line(bs, "fin_debt_c").id,
        })
        self.assertValues(self._it_run(), 2026, {"fin_debt_nc": 0, "fin_debt_c": 375000})


@tagged("post_install", "-at_install")
class TestFinExcludedJournals(FinAnalysisCommon):
    """RN-002: moves of the run's excluded journals (e.g. year-end closing) are ignored by the GL read."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.closing_journal = cls.env["account.journal"].with_company(cls.company).create({
            "name": "Year-end closing", "code": "CLO", "type": "general", "company_id": cls.company.id,
        })
        pl_codes = [code for code in MOVES_2026 if not cls.acc[code].include_initial_balance]
        closing = {code: -MOVES_2026[code] for code in pl_codes}
        closing["FXCAP"] = sum(MOVES_2026[code] for code in pl_codes)  # -116,000: 2026 result closed to equity
        move = cls.env["account.move"].with_company(cls.company).create({
            "move_type": "entry", "date": date(2026, 12, 31), "journal_id": cls.closing_journal.id,
            "line_ids": [
                Command.create({
                    "name": code, "account_id": cls.acc[code].id,
                    "debit": amount if amount > 0 else 0.0, "credit": -amount if amount < 0 else 0.0,
                })
                for code, amount in closing.items() if amount
            ],
        })
        move.action_post()

    def test_rn002_closing_entry_zeroes_pl_without_exclusion(self):
        # RN-002 (precondition): the closing entry really empties the 2026 P&L when not excluded
        run = self._run(2026, 2026)
        run.action_compute()
        self.assertValues(run, 2026, {"revenue": 0, "net_income": 0, "equity": 274000})

    def test_rn002_excluded_journal_restores_pre_closing_results(self):
        # RN-002: excluding the closing journal gives the pre-closing figures of the fixture
        run = self._run(2025, 2026, excluded_journal_ids=[Command.set(self.closing_journal.ids)])
        run.action_compute()
        self.assertValues(run, 2026, {
            "revenue": 1200000, "value_added": 503000, "ebitda": 233000, "mon": 162000, "net_income": 116000,
            "equity": 274000, "total_assets": 557000, "coin": 277000, "pfn": 3000,
        })
        self.assertValues(run, 2025, {"revenue": 1100000, "net_income": 88000, "equity": 188000})
        self.assertRatios(run, 2026, {"roe": 116000 / 274000})

    def test_rn002_excluded_journals_is_a_run_input(self):
        # RN-002 / AD-113: changing the excluded journals changes the snapshot inputs
        self.assertIn("excluded_journal_ids", self.env["cmc.fin.analysis"]._cmc_input_fields)

    def test_rn002_excluded_journal_of_other_company_rejected(self):
        # RN-002 / AD-115: check_company on the excluded journals
        with self.assertRaises(UserError):
            self._run(2026, 2026, excluded_journal_ids=[Command.set(self.company_data_b["default_journal_misc"].ids)])


@tagged("post_install", "-at_install")
class TestFinStrictNames(FinAnalysisCommon):
    """RN-003: a typo in a formula or ratio is an error, not a silently missing value."""

    def _probe_scheme(self, formula):
        return self.env["cmc.fin.scheme"].create({
            "name": "Probe", "code": "probe_cf", "scheme_type": "cash_flow", "sequence": 999, "company_id": self.company.id,
            "line_ids": [Command.create({"code": "probe_x", "name": "Probe", "line_type": "formula", "formula": formula})],
        })

    def test_rn003_formula_line_unknown_name_raises(self):
        # RN-003: unknown name in a line formula -> UserError naming the line and the name
        self._probe_scheme("revenue + no_such_name")
        run = self._run(2026, 2026)
        with self.assertRaises(UserError) as caught:
            run.action_compute()
        self.assertIn("probe_x", str(caught.exception))
        self.assertIn("no_such_name", str(caught.exception))

    def test_rn003_ratio_unknown_name_raises(self):
        # RN-003: unknown name in a ratio -> UserError naming the ratio and the name
        self.env["cmc.fin.ratio"].create({
            "code": "probe_ratio", "name": "Probe ratio", "area": "liquidity", "expression": "revenue / no_such_input",
            "company_id": self.company.id,
        })
        run = self._run(2026, 2026)
        with self.assertRaises(UserError) as caught:
            run.action_compute()
        self.assertIn("probe_ratio", str(caught.exception))
        self.assertIn("no_such_input", str(caught.exception))

    def test_rn003_known_input_or_prev_missing_stays_not_computable(self):
        # RN-003: an input code typed for another year, or prev_* in the first year, is NOT an unknown name
        self.env["cmc.fin.input"].create({"company_id": self.company.id, "year": 2026, "code": "probe_factor", "value": 2.0})
        self.env["cmc.fin.ratio"].create({
            "code": "probe_scaled", "name": "Scaled", "area": "value", "unit": "amount", "expression": "revenue * probe_factor",
            "company_id": self.company.id,
        })
        run = self._run(2025, 2026)
        run.action_compute()
        self.assertEqual(self._ratio(run, "probe_scaled", 2025).status, "not_computable")
        self.assertRatios(run, 2026, {"probe_scaled": 2400000.0}, places=2)
        self.assertEqual(self._ratio(run, "roe_avg", 2025).status, "not_computable")  # prev_equity: 2025 is the first run year
        self.assertEqual(self._ratio(run, "wacc", 2025).status, "not_computable")  # shipped inputs typed for 2026 only

    def test_rn003_formula_dependency_from_unselected_scheme_raises(self):
        # RN-003: the functional cash flow needs the P&L lines; without that scheme the run must say so
        schemes = self.schemes["scheme_bs_functional"] | self.schemes["scheme_cf_functional"]
        run = self._run(2026, 2026, scheme_ids=[Command.set(schemes.ids)])
        with self.assertRaises(UserError) as caught:
            run.action_compute()
        message = str(caught.exception)
        self.assertIn("cf_gross_self_financing", message)
        self.assertRegex(message, r"\bmon\b")

    def test_rn003_ratio_on_unselected_scheme_stays_not_computable(self):
        # RN-003: ratios are a catalogue over every scheme; a ratio on a scheme not in the run is n.c., not an error
        run = self._run(2026, 2026, scheme_ids=[Command.set(self.schemes["scheme_bs_functional"].ids)])
        run.action_compute()
        self.assertEqual(self._ratio(run, "roe", 2026).status, "not_computable")
        self.assertRatios(run, 2026, {"current_ratio": 306000 / 143000})

