"""WP-08 / ABM-05 - benchmark of unit activity costs across companies and periods on a common dictionary.

CONTRACT (see abm_common)
- ``cmc.abm.benchmark`` (company_id optional) with ``run_ids`` and ``line_ids`` -> ``cmc.abm.benchmark.line``
  (run_id, company_id, period, activity_code, activity_id, unit_cost = activity rate, best_unit_cost, gap_percent,
  is_best); ``action_compute()`` replaces the lines (activity costs with used_qty > 0); a draft run -> UserError.
  Common dictionary = activity ``code``. Lines follow the company rule of their run.
Hand figures: A March QC 2500 / 5 = 500; B March QC (code WP07QC) 1800 / 4 = 450 -> best 450, A gap 11.1111 %;
A April QC 9999 / 3 = 3333 -> gap 640.6667 %. MACH only in A March: 62.8, best of itself, gap 0.
MGMT (sustaining, no driver quantity) is not benchmarked.
"""

from odoo.exceptions import UserError
from odoo.tests.common import new_test_user, tagged

from .abm_common import AbmCase
from .abc2_common import D_FROM, D_MID, D_TO


@tagged("post_install", "-at_install")
class TestAbmBenchmark(AbmCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company_b = cls.env["res.company"].create({"name": "CMC WP08 B"})
        env_b = cls.env_b = cls._company_env(cls.company_b)
        sal_b = env_b["cmc.cost.element"].create({"name": "WP08 B sal", "code": "WP08BSAL", "company_id": cls.company_b.id})
        zb = cls._center(env_b, "WP08 ZB", cls.company_b)
        cls._cost(env_b, zb, 1800, sal_b, "fixed", day=D_MID)
        insp_b = cls._driver(env_b, "WP07INSP", "transaction", level="batch", company=cls.company_b)
        cls.qc_b = cls._activity("Inspect products", "WP07QC", False, "batch", insp_b, env=env_b)
        res_b = env_b["cmc.abc.resource"].create({"name": "ZB people", "code": "WP08RB", "center_id": zb.id})
        prod = cls.env["product.product"].create({"name": "WP08 B product"})
        obj_b = env_b["cmc.cost.object"]._cmc_ensure_objects(prod, company=cls.company_b)
        cls.run_b = env_b["cmc.abc.run"].create({
            "name": "WP08 B March", "company_id": cls.company_b.id, "date_from": D_FROM, "date_to": D_TO,
            "resource_line_ids": [(0, 0, {"resource_id": res_b.id, "activity_id": cls.qc_b.id, "percent": 100})],
            "line_ids": [(0, 0, {"activity_id": cls.qc_b.id, "cost_object_id": obj_b.id, "driver_id": insp_b.id, "driver_qty": 4})],
        })
        cls.run_b.action_compute()
        both = cls.company_a | cls.company_b
        cls.manager_ab = new_test_user(cls.env, login="wp08_bench_manager", groups="base.group_user,cmc_base.group_cmc_manager",
                                       company_id=cls.company_a.id, company_ids=[(6, 0, both.ids)])
        cls.user_a = new_test_user(cls.env, login="wp08_bench_user_a", groups="base.group_user,cmc_base.group_cmc_user",
                                   company_id=cls.company_a.id, company_ids=[(6, 0, cls.company_a.ids)])

    def _env_ab(self):
        return self.env(user=self.manager_ab, context=dict(self.env.context, allowed_company_ids=(self.company_a | self.company_b).ids))

    def _benchmark(self, runs):
        bench = self._env_ab()["cmc.abm.benchmark"].create({"name": "WP08 bench", "run_ids": [(6, 0, runs.ids)]})
        bench.action_compute()
        return bench

    def _line(self, bench, run, code):
        return bench.line_ids.filtered(lambda line: line.run_id == run and line.activity_code == code)

    def test_abm05_unit_cost_gap_to_best_across_companies(self):
        # ABM-05 / Turney Ch.5: same activity code, unit cost compared with the best site
        run_a = self._main_run()
        bench = self._benchmark(run_a | self.run_b)
        self.assertRecordValues(self._line(bench, run_a, "WP07QC") | self._line(bench, self.run_b, "WP07QC"), [
            {"company_id": self.company_a.id, "period": D_FROM, "unit_cost": 500.0, "best_unit_cost": 450.0, "is_best": False},
            {"company_id": self.company_b.id, "period": D_FROM, "unit_cost": 450.0, "best_unit_cost": 450.0, "is_best": True},
        ])
        self.assertAlmostEqual(self._line(bench, run_a, "WP07QC").gap_percent, 11.1111, places=3)
        self.assertEqual(self._line(bench, self.run_b, "WP07QC").gap_percent, 0.0)

    def test_abm05_periods_of_one_company(self):
        # ABM-05: the same dictionary compares periods too (March vs April of company A)
        run_a, april = self._main_run(), self._april_run()
        bench = self._benchmark(run_a | april | self.run_b)
        self.assertAlmostEqual(self._line(bench, april, "WP07QC").unit_cost, 3333.0, places=2)
        self.assertAlmostEqual(self._line(bench, april, "WP07QC").gap_percent, 640.6667, places=3)

    def test_abm05_activity_without_peer_and_sustaining(self):
        # ABM-05: a code with one site is its own best; activities without driver volume are not benchmarked
        run_a = self._main_run()
        bench = self._benchmark(run_a | self.run_b)
        mach = self._line(bench, run_a, "WP07MACH")
        self.assertRecordValues(mach, [{"unit_cost": 62.8, "best_unit_cost": 62.8, "gap_percent": 0.0, "is_best": True}])
        self.assertFalse(self._line(bench, run_a, "WP07MGMT"))

    def test_abm05_recompute_replaces_lines(self):
        # ABM-05: compute is idempotent
        bench = self._benchmark(self._main_run() | self.run_b)
        count = len(bench.line_ids)
        bench.action_compute()
        self.assertEqual(len(bench.line_ids), count)

    def test_abm05_draft_run_refused(self):
        # ABM-05: only certified (done) runs are benchmarked
        draft = self._main_run(compute=False)
        bench = self._env_ab()["cmc.abm.benchmark"].create({"name": "WP08 draft", "run_ids": [(6, 0, (draft | self.run_b).ids)]})
        with self.assertRaises(UserError):
            bench.action_compute()

    def test_abm05_single_company_user_sees_own_lines_only(self):
        # ABM-05 / AD-115: a user of company A never reads the unit costs of company B
        bench = self._benchmark(self._main_run() | self.run_b)
        env = self.env(user=self.user_a, context=dict(self.env.context, allowed_company_ids=self.company_a.ids))
        lines = env["cmc.abm.benchmark.line"].search([("benchmark_id", "=", bench.id)])
        self.assertTrue(lines)
        self.assertEqual(lines.company_id, self.company_a)
