"""Stress test 2026-10 (rilancio) - sector benchmark typed as read in the sector study on cmc_fin_analysis.

``cmc.fin.benchmark`` gains ``unit`` (related to the ratio) and ``value_input`` (compute + inverse): a percent ratio is
typed as a percentage (7.5 = 7.5 %) and stored as the fraction the engine compares (0.075); any other unit is typed
and stored as is.
"""

from odoo import Command
from odoo.tests.common import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestFinStressRilancio(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        Ratio = cls.env["cmc.fin.ratio"]
        cls.roe = Ratio.search([("code", "=", "roe"), ("company_id", "=", False)], limit=1)
        cls.current = Ratio.search([("code", "=", "current_ratio"), ("company_id", "=", False)], limit=1)
        # the benchmark is entered by a CMC user, who holds write access on it
        cls.cmc_user = new_test_user(
            cls.env, login="fin_rl_user", groups="base.group_user,cmc_base.group_cmc_user",
            company_id=cls.company.id, company_ids=[Command.set(cls.company.ids)],
        )

    def _benchmark(self, ratio, value_input):
        return self.env["cmc.fin.benchmark"].with_user(self.cmc_user).create({
            "company_id": self.company.id, "ateco_code": "25.62", "year": 2026, "ratio_id": ratio.id, "value_input": value_input,
        })

    def test_percent_benchmark_typed_as_percentage_stored_as_fraction(self):
        # FX benchmark: ROE 7.5 typed from the sector study is 7.5 %, i.e. the fraction 0.075 the ratios use
        self.assertEqual(self.roe.unit, "percent")
        benchmark = self._benchmark(self.roe, 7.5)
        self.assertRecordValues(benchmark, [{"unit": "percent"}])
        self.assertAlmostEqual(benchmark.value, 0.075, places=6)
        self.assertAlmostEqual(benchmark.value_input, 7.5, places=6)

    def test_ratio_benchmark_stored_as_typed(self):
        # FX benchmark: a plain ratio (current ratio 1.2) is not rescaled
        self.assertEqual(self.current.unit, "ratio")
        benchmark = self._benchmark(self.current, 1.2)
        self.assertRecordValues(benchmark, [{"unit": "ratio"}])
        self.assertAlmostEqual(benchmark.value, 1.2, places=6)
        self.assertAlmostEqual(benchmark.value_input, 1.2, places=6)

    def test_percent_benchmark_stored_value_reads_back_as_percentage(self):
        # FX benchmark: a fraction stored before the fix (0.15) is shown as 15 (%) in the input column
        benchmark = self.env["cmc.fin.benchmark"].create({
            "company_id": self.company.id, "ateco_code": "25.62", "year": 2026, "ratio_id": self.roe.id, "value": 0.15,
        })
        self.assertAlmostEqual(benchmark.with_user(self.cmc_user).value_input, 15.0, places=6)
