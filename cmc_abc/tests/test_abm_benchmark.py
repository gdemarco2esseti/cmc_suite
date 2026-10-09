"""WP-08 / ABM-05 - benchmark of unit activity costs across companies and periods on a common dictionary.

CONTRACT (see abm_common)
- ``cmc.abm.benchmark`` (company_id optional) with ``run_ids`` and ``line_ids`` -> ``cmc.abm.benchmark.line``
  (run_id, company_id, period, activity_code, activity_id, unit_cost = activity rate, best_unit_cost, gap_percent,
  is_best); ``action_compute()`` replaces the lines (activity costs with used_qty > 0); a draft run -> UserError.
  Common dictionary = activity ``code``.
- RN-001 (cross-company document): ``company_ids`` M2m res.company, stored computed = ``run_ids.company_id``.
  Record rule on the benchmark AND on its lines: readable only when EVERY company of the benchmark is in the
  user's active ``company_ids`` (a user of A never reads a benchmark spanning A and B, not even A's own line:
  its ``best_unit_cost`` is B's figure). ``action_compute()``: UserError (before deleting anything) when a run
  belongs to a company outside ``env.companies`` (no silent filtering, other companies' lines untouched) and
  when the selected runs carry different currencies (no implicit FX).
Hand figures: A March QC 2500 / 5 = 500; B March QC (code WP07QC) 1800 / 4 = 450 -> best 450, A gap 11.1111 %;
A April QC 9999 / 3 = 3333 -> gap 640.6667 %. MACH only in A March: 62.8, best of itself, gap 0.
MGMT (sustaining, no driver quantity) is not benchmarked.
"""

from odoo.exceptions import AccessError, UserError
from odoo.tests.common import new_test_user, tagged

from .abm_common import AbmCase
from .abc2_common import D_FROM, D_MID, D_TO


@tagged("post_install", "-at_install")
class TestAbmBenchmark(AbmCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company_b = cls.env["res.company"].create({"name": "CMC WP08 B", "currency_id": cls.company_a.currency_id.id})
        cls.env_b = cls._company_env(cls.company_b)
        cls.run_b, cls.qc_b = cls._peer_run(cls.company_b, "WP08 B", 1800, 4)
        both = cls.company_a | cls.company_b
        cls.manager_ab = new_test_user(cls.env, login="wp08_bench_manager", groups="base.group_user,cmc_base.group_cmc_manager",
                                       company_id=cls.company_a.id, company_ids=[(6, 0, both.ids)])
        cls.user_a = new_test_user(cls.env, login="wp08_bench_user_a", groups="base.group_user,cmc_base.group_cmc_user",
                                   company_id=cls.company_a.id, company_ids=[(6, 0, cls.company_a.ids)])

    @classmethod
    def _peer_run(cls, company, tag, cost, inspections):
        """A computed March run of another company: one QC activity (code WP07QC) fed by one center."""
        env = cls._company_env(company)
        sal = env["cmc.cost.element"].create({"name": "%s sal" % tag, "code": "WP08BSAL", "company_id": company.id})
        center = cls._center(env, "%s Z" % tag, company)
        cls._cost(env, center, cost, sal, "fixed", day=D_MID)
        insp = cls._driver(env, "WP07INSP", "transaction", level="batch", company=company)
        qc = cls._activity("Inspect products", "WP07QC", False, "batch", insp, env=env)
        res = env["cmc.abc.resource"].create({"name": "%s people" % tag, "code": "WP08RB", "center_id": center.id})
        prod = cls.env["product.product"].create({"name": "%s product" % tag})
        obj = env["cmc.cost.object"]._cmc_ensure_objects(prod, company=company)
        run = env["cmc.abc.run"].create({
            "name": "%s March" % tag, "company_id": company.id, "date_from": D_FROM, "date_to": D_TO,
            "resource_line_ids": [(0, 0, {"resource_id": res.id, "activity_id": qc.id, "percent": 100})],
            "line_ids": [(0, 0, {"activity_id": qc.id, "cost_object_id": obj.id, "driver_id": insp.id, "driver_qty": inspections})],
        })
        run.action_compute()
        return run, qc

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

    def test_abm05_single_company_user_reads_a_benchmark_of_his_company(self):
        # ABM-05 / AD-115: a benchmark of company A periods only is fully readable by a user of A
        run_a, april = self._main_run(), self._april_run()
        bench = self._benchmark(run_a | april)
        env = self.env(user=self.user_a, context=dict(self.env.context, allowed_company_ids=self.company_a.ids))
        self.assertEqual(env["cmc.abm.benchmark"].search([("id", "=", bench.id)]), bench.with_env(env))
        lines = env["cmc.abm.benchmark.line"].search([("benchmark_id", "=", bench.id)])
        self.assertEqual(len(lines), len(bench.line_ids))
        self.assertEqual(lines.company_id, self.company_a)

    # ---- RN-001: a benchmark spanning companies is a cross-company document ----

    def _env_user_a(self):
        return self.env(user=self.user_a, context=dict(self.env.context, allowed_company_ids=self.company_a.ids))

    def test_rn001_benchmark_companies_are_the_companies_of_its_runs(self):
        # RN-001: the companies of a benchmark are those of its runs (stored, the record rule reads them)
        bench = self._benchmark(self._main_run() | self.run_b)
        self.assertEqual(bench.company_ids, self.company_a | self.company_b)

    def test_rn001_single_company_user_cannot_read_a_cross_company_benchmark(self):
        # RN-001 / AD-115: the best unit cost of a cross-company benchmark is B's figure: user A must not read it
        bench = self._benchmark(self._main_run() | self.run_b)
        env = self._env_user_a()
        self.assertFalse(env["cmc.abm.benchmark"].search([("id", "=", bench.id)]))
        with self.assertRaises(AccessError):
            env["cmc.abm.benchmark"].browse(bench.id).read(["name"])

    def test_rn001_best_unit_cost_never_exposed_to_single_company_user(self):
        # RN-001: not even A's own line (whose best_unit_cost 450 is company B's unit cost) is readable by user A
        run_a = self._main_run()
        bench = self._benchmark(run_a | self.run_b)
        own_line = self._line(bench, run_a, "WP07QC")
        self.assertAlmostEqual(own_line.best_unit_cost, 450.0, places=2)
        env = self._env_user_a()
        self.assertFalse(env["cmc.abm.benchmark.line"].search([("benchmark_id", "=", bench.id)]))
        self.assertFalse(env["cmc.abm.benchmark.line"].search([("best_unit_cost", "=", 450.0)]))
        with self.assertRaises(AccessError):
            env["cmc.abm.benchmark.line"].browse(own_line.id).read(["best_unit_cost"])

    def test_rn001_manager_with_one_active_company_cannot_compute(self):
        # RN-001: the multi-company manager working in company A only does not reach a benchmark spanning A and B
        bench = self._benchmark(self._main_run() | self.run_b)
        with self.assertRaises(AccessError):
            bench.with_user(self.manager_ab).with_context(allowed_company_ids=self.company_a.ids).action_compute()

    def test_rn001_compute_refuses_runs_outside_the_active_companies(self):
        # RN-001: no silent filtering: a run of a company not in env.companies is an error, not a shorter benchmark
        bench = self._benchmark(self._main_run() | self.run_b)
        with self.assertRaises(UserError):
            bench.with_env(self.env_a).action_compute()

    def test_rn001_failed_compute_keeps_the_lines_of_other_companies(self):
        # RN-001: the refused compute deletes nothing (B's lines are not wiped by a user working in A)
        bench = self._benchmark(self._main_run() | self.run_b)
        b_lines = bench.line_ids.filtered(lambda line: line.company_id == self.company_b)
        self.assertTrue(b_lines)
        try:
            bench.with_env(self.env_a).action_compute()
        except UserError:
            pass
        self.assertTrue(b_lines.exists())
        self.assertEqual(bench.line_ids.filtered(lambda line: line.company_id == self.company_b), b_lines)

    def test_rn001_mixed_currencies_refused(self):
        # RN-001: unit costs in different currencies are not comparable (no silent FX): UserError
        other = self.env["res.currency"].with_context(active_test=False).search(
            [("id", "!=", self.company_a.currency_id.id)], limit=1
        )
        other.active = True
        company_c = self.env["res.company"].create({"name": "CMC WP08 C", "currency_id": other.id})
        run_c, _qc_c = self._peer_run(company_c, "WP08 C", 1800, 4)
        env = self.env(context=dict(self.env.context, allowed_company_ids=(self.company_a | company_c).ids))
        bench = env["cmc.abm.benchmark"].create({"name": "WP08 fx", "run_ids": [(6, 0, (self._main_run() | run_c).ids)]})
        with self.assertRaises(UserError):
            bench.action_compute()

    # ---- RN-002: a benchmarked run is a certified source ------------------------

    def test_rn002_reset_of_a_benchmarked_run_refused(self):
        # RN-002 / AD-002: a run read by a benchmark cannot lose its results under it (dependents check)
        run_a = self._main_run()
        self._benchmark(run_a | self.run_b)
        with self.assertRaises(UserError):
            run_a.action_reset_to_draft()
        with self.assertRaises(UserError):
            self.run_b.with_env(self.env_b).action_reset_to_draft()

    def test_rn002_reset_allowed_once_the_benchmark_is_gone(self):
        # RN-002: the guard is the dependency, not the run: drop the benchmark and the reset works again
        run_a = self._main_run()
        bench = self._benchmark(run_a | self.run_b)
        bench.unlink()
        run_a.action_reset_to_draft()
        self.assertEqual(run_a.state, "draft")
