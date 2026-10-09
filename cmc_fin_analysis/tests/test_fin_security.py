"""WP-13 security and multi-company (AD-115), executed AS the real roles (``with_user``).

CONTRACT: see ``tests/common.py`` (ACL table and record rules). Summary:
- ``group_cmc_user`` creates and computes runs (the engine reads the GL through the gated readers, sudo
  inside), records choices (overrides), history, inputs and benchmarks; reads schemes / rules / ratios and
  results; cannot configure schemes, rules or ratios, cannot write results.
- ``group_cmc_manager`` configures schemes, rules and ratios.
- A user outside the CMC groups gets AccessError on runs and results.
- Company rule ``company_id parent_of company_ids`` (shared rows company False): a user of company B
  neither sees nor computes a run of company A, and a run only reads its own company (GL and history).
"""

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged
from odoo.tests.common import new_test_user

from .common import FinAnalysisCommon


@tagged("post_install", "-at_install")
class TestFinSecurity(FinAnalysisCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cmc_manager = new_test_user(
            cls.env, login="fin_cmc_manager", groups="base.group_user,cmc_base.group_cmc_manager",
            company_id=cls.company.id, company_ids=[Command.set(cls.company.ids)],
        )

    def test_cmc_user_creates_and_computes_run_as_itself(self):
        # AD-115: the analyst runs the analysis without accounting rights (reader gate + sudo aggregation)
        run = self._run(2025, 2026, user=self.cmc_user)
        run.action_compute()
        self.assertEqual(run.state, "done")
        self.assertAlmostEqual(self._val(run, "net_income", 2026), 116000, places=2)
        self.assertAlmostEqual(self._ratio(run, "roe", 2026).value, 116000 / 274000, places=6)

    def test_cmc_user_cannot_write_results(self):
        # AD-001: results are written by Compute only, never by hand
        run = self._run(2026, 2026, user=self.cmc_user)
        run.action_compute()
        with self.assertRaises((AccessError, UserError)):
            run.value_ids[:1].write({"value": 1.0})
        with self.assertRaises((AccessError, UserError)):
            run.ratio_value_ids[:1].write({"value": 1.0})

    def test_cmc_user_records_choices_history_and_inputs(self):
        # FIN-02 / AD-116: overrides, imported balances and typed inputs are analyst data entry
        bs = self.schemes["scheme_bs_functional"]
        env = self.env(user=self.cmc_user)
        env["cmc.fin.override"].create({
            "company_id": self.company.id, "scheme_id": bs.id, "year": 2026, "account_id": self.acc["FXMUT"].id,
            "line_id": self._line(bs, "fin_debt_c").id, "amount": 20000, "reason": "Short-term portion",
        })
        env["cmc.fin.history"].create({"company_id": self.company.id, "year": 2021, "account_id": self.acc["FXBNK"].id, "balance": 0.0})
        env["cmc.fin.input"].create({"company_id": self.company.id, "year": 2025, "code": "employees", "value": 9})

    def test_cmc_user_cannot_configure_schemes_or_ratios(self):
        # AD-115: schemes, rules and ratios are configured by the CMC manager only
        bs = self.schemes["scheme_bs_functional"]
        env = self.env(user=self.cmc_user)
        self.assertTrue(env["cmc.fin.scheme"].browse(bs.id).line_ids)  # readable
        with self.assertRaises(AccessError):
            env["cmc.fin.scheme.line"].browse(self._line(bs, "coin").id).write({"formula": "net_fixed"})
        with self.assertRaises(AccessError):
            env["cmc.fin.mapping.rule"].create({
                "scheme_id": bs.id, "company_id": self.company.id, "account_code_prefix": "FXBNK",
                "line_id": self._line(bs, "cash").id,
            })
        with self.assertRaises(AccessError):
            env["cmc.fin.ratio"].create({"code": "my_ratio", "name": "Mine", "area": "value", "expression": "coin", "company_id": self.company.id})

    def test_cmc_manager_configures_company_ratio(self):
        # AD-115 / AD-116: a manager adds a company ratio; the next run evaluates it
        env = self.env(user=self.cmc_manager)
        env["cmc.fin.ratio"].create({
            "code": "equity_to_coin", "name": "Equity / COIN", "area": "solidity", "sequence": 999,
            "expression": "equity / coin", "company_id": self.company.id,
        })
        run = self._run(2026, 2026, user=self.cmc_manager)
        run.action_compute()
        self.assertAlmostEqual(self._ratio(run, "equity_to_coin", 2026).value, 274000 / 277000, places=6)

    def test_outsider_cannot_read_or_compute(self):
        # AD-115: no CMC group, no financial analysis
        run = self._run(2026, 2026)
        with self.assertRaises(AccessError):
            self.env["cmc.fin.analysis"].with_user(self.outsider).search([])
        with self.assertRaises(AccessError):
            run.with_user(self.outsider).action_compute()
        run.action_compute()
        with self.assertRaises(AccessError):
            run.value_ids.with_user(self.outsider).read(["value"])

    def test_other_company_user_cannot_see_or_compute_run(self):
        # AD-115: company record rule on the run and its results
        run = self._run(2026, 2026)
        run.action_compute()
        Run = self.env["cmc.fin.analysis"].with_user(self.cmc_user_b)
        self.assertFalse(Run.search([("id", "=", run.id)]))
        self.assertFalse(self.env["cmc.fin.analysis.value"].with_user(self.cmc_user_b).search([("run_id", "=", run.id)]))
        with self.assertRaises(AccessError):
            run.with_user(self.cmc_user_b).read(["name"])
        with self.assertRaises(AccessError):
            Run.create({"name": "Intrusion", "company_id": self.company.id, "year_from": 2026, "year_to": 2026})

    def test_company_run_reads_only_its_company(self):
        # AD-115: company B run sees B history and GL only; A rules and history do not leak either way
        data_b = self.company_data_b
        History = self.env["cmc.fin.history"].with_user(self.cmc_user_b)
        History.create([
            {"company_id": self.company_b.id, "year": 2024, "account_id": data_b["default_account_revenue"].id, "balance": -500.0},
            {"company_id": self.company_b.id, "year": 2024, "account_id": data_b["default_journal_bank"].default_account_id.id, "balance": 500.0},
        ])
        run_b = self.env["cmc.fin.analysis"].with_user(self.cmc_user_b).create({
            "name": "B 2024", "company_id": self.company_b.id, "year_from": 2024, "year_to": 2024,
        })
        run_b.action_compute()
        self.assertAlmostEqual(self._val(run_b, "revenue", 2024), 500.0, places=2)
        self.assertAlmostEqual(self._val(run_b, "cash", 2024), 500.0, places=2)
        self.assertAlmostEqual(self._val(run_b, "equity", 2024), 500.0, places=2)
        run_a = self._run(2024, 2024, user=self.cmc_user)
        run_a.action_compute()
        self.assertAlmostEqual(self._val(run_a, "revenue", 2024), 1000000, places=2)
