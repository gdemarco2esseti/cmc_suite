"""RN-002 (ronin on WP-14): the pack of a computed run is a snapshot, and its sources are pinned (AD-002, AD-113).

Run Jan-Feb 2026, bva_version_id = the approved fixture budget, fin_analysis_id = the fixture analysis.
  BvA "KPI production" Feb: plan -1,000, actual -1,200 (-20 %); Jan +10 %. current_ratio 2026 = 2.0.
  A back-dated posting of 300 on Feb 20 moves the LIVE summary to actual -1,500 (-50 %) but not the run pack.
"""

from datetime import date

from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.cmc_base.models.cmc_run_mixin import CMC_RUN_STATE

from .common import FEB, JAN, KpiCase


@tagged("post_install", "-at_install")
class TestKpiSnapshot(KpiCase):
    def _pack_run(self, **vals):
        lib = self._library()
        vals = {"bva_version_id": self.budget.id, "fin_analysis_id": self.fin.id, **vals}
        return lib, self._run(JAN, FEB, **vals)

    @staticmethod
    def _production(rows, center):
        return next(row for row in rows if row["center_id"] == center)

    def test_bva_rows_copied_per_month(self):
        # RN-002: compute copies the BvA block of every month of the span into the run
        _lib, run = self._pack_run()
        rows = run.bva_ids.filtered(lambda row: row.center_id == self.production)
        self.assertEqual(set(rows.mapped("date")), {JAN, FEB})
        jan, feb = rows.filtered(lambda row: row.date == JAN), rows.filtered(lambda row: row.date == FEB)
        self.assertAlmostEqual(jan.variance_month_pct, 10.0, places=2)
        self.assertRecordValues(feb, [{"plan_month": -1000.0, "actual_month": -1200.0, "variance_month_pct": -20.0}])

    def test_pack_bva_frozen_after_backdated_posting(self):
        # RN-002: a later back-dated posting changes the live BvA, never the re-printed pack of a computed run
        _lib, run = self._pack_run()
        before = self._production(run._cmc_pack_data()["bva"], self.production)
        self._cost(self.production, 300.0, date(2026, 2, 20))
        self.env.flush_all()
        live = self._production(self.budget._cmc_bva_summary(date(2026, 2, 28), ("center_id",)), self.production)
        self.assertAlmostEqual(live["actual_month"], -1500.0, places=2, msg="the fixture must move the live summary")
        after = self._production(run._cmc_pack_data()["bva"], self.production)
        self.assertAlmostEqual(after["actual_month"], -1200.0, places=2)
        self.assertAlmostEqual(after["variance_month_pct"], -20.0, places=2)
        self.assertEqual(after, before)

    def test_ratio_rows_copied(self):
        # RN-002: compute copies the ratio values of the analysis used into the run
        _lib, run = self._pack_run()
        row = run.ratio_ids.filtered(lambda row: row.code == "current_ratio")
        self.assertRecordValues(row, [{"fin_analysis_id": self.fin.id, "year": 2026, "value": 2.0}])

    def test_pack_ratios_read_from_the_run(self):
        # RN-002: the pack reads the copied ratios, not the live analysis rows
        _lib, run = self._pack_run()
        # simulated drift of the live ratio row (the engine sentinel, as a recompute would): the pack must not see it
        live = self.env["cmc.fin.ratio.value"].search([("run_id", "=", self.fin.id), ("year", "=", 2026), ("code", "=", "current_ratio")])
        live.with_context(cmc_run_state=CMC_RUN_STATE).sudo().write({"value": 9.0})
        ratio = next(row for row in run._cmc_pack_data()["ratios"] if row["code"] == "current_ratio")
        self.assertAlmostEqual(ratio["value"], 2.0, places=6)

    def test_ratios_from_the_fallback_analysis(self):
        # RN-002: without fin_analysis_id the pack shows the ratios of the analysis its ratio KPIs read
        _lib, run = self._pack_run(fin_analysis_id=False)
        self.assertEqual(run.ratio_ids.fin_analysis_id, self.fin)
        ratio = next(row for row in run._cmc_pack_data()["ratios"] if row["code"] == "current_ratio")
        self.assertAlmostEqual(ratio["value"], 2.0, places=6)

    def test_reset_deletes_the_snapshot(self):
        # AD-113: reset deletes the copied blocks with the other results
        _lib, run = self._pack_run()
        self.assertTrue(run.bva_ids and run.ratio_ids)
        run.action_reset_to_draft()
        self.assertFalse(run.bva_ids or run.ratio_ids)

    def test_usages_on_pack_sources(self):
        # RN-002 / AD-002: the analysis and the budget named by the run are pinned, even with no KPI reading them
        lib = self._library()
        run = self._run(JAN, FEB, kpi_ids=[(6, 0, lib["otd"].ids)], bva_version_id=self.budget.id, fin_analysis_id=self.fin.id)
        self.assertLessEqual({("cmc.fin.analysis", self.fin.id), ("cmc.plan.version", self.budget.id)}, self._usages(run))
        with self.assertRaises(UserError):
            self.fin.action_reset_to_draft()
        with self.assertRaises(UserError):
            self.budget.with_user(self.manager).action_reset_to_draft()

    def test_usage_on_variance_version(self):
        # RN-002 / AD-002: the budget a variance KPI reads is pinned (no bva_version_id on the run)
        _lib = self._library()
        run = self._run(JAN)
        self.assertIn(("cmc.plan.version", self.budget.id), self._usages(run))
        with self.assertRaises(UserError):
            self.budget.with_user(self.manager).action_reset_to_draft()
        run.action_reset_to_draft()
        self.budget.with_user(self.manager).action_reset_to_draft()
        self.assertEqual(self.budget.state, "draft")
