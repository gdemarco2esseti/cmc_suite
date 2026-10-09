"""KPI-04 monthly management pack: KPI table + BSC + BvA summary + key ratios, data and PDF (smoke render).

Month Feb 2026 (run Jan-Feb): otd 100 green improving; rent BvA summary of "KPI production": plan -1,000, actual
-1,200, variance -200 (-20 %); current_ratio 2026 = 2.0 from the analysis used.
In test mode ``_render_qweb_pdf`` falls back to HTML (no wkhtmltopdf): the HTML is what is asserted.
"""

from markupsafe import escape

from odoo.tests import tagged

from .common import FEB, JAN, KpiCase

REPORT_XMLID = "cmc_kpi.action_report_cmc_kpi_pack"


@tagged("post_install", "-at_install")
class TestKpiPack(KpiCase):
    def _pack_run(self):
        lib = self._library()
        scorecard = self.env["cmc.kpi.scorecard"].create({
            "name": "KPI pack scorecard",
            "perspective_ids": [(0, 0, {"perspective": "customer", "weight": 100.0})],
            "line_ids": [(0, 0, {"kpi_id": lib["otd"].id, "weight": 100.0})],
        })
        run = self._run(JAN, FEB, bva_version_id=self.budget.id, fin_analysis_id=self.fin.id)
        return lib, scorecard, run

    def test_pack_data_month_and_kpis(self):
        # KPI-04: the pack is the month of date_to by default, one entry per KPI of the run
        lib, _scorecard, run = self._pack_run()
        data = run.with_user(self.user)._cmc_pack_data()
        self.assertEqual(data["month"], FEB)
        self.assertEqual(len(data["kpis"]), len(lib))
        otd = next(row for row in data["kpis"] if row["kpi"] == lib["otd"])
        self.assertAlmostEqual(otd["value"], 100.0, places=4)
        self.assertEqual((otd["status"], otd["trend"], otd["computable"]), ("green", "improving", True))

    def test_pack_kpis_grouped_by_perspective(self):
        # KPI-04: financial first, then customer, internal, learning
        _lib, _scorecard, run = self._pack_run()
        order = [row["kpi"].perspective for row in run._cmc_pack_data()["kpis"]]
        rank = {"financial": 0, "customer": 1, "internal": 2, "learning": 3}
        self.assertEqual(order, sorted(order, key=rank.get))

    def test_pack_scorecard(self):
        # KPI-04: BSC block with overall and perspective scores
        _lib, scorecard, run = self._pack_run()
        block = run._cmc_pack_data()["scorecards"]
        self.assertEqual([entry["scorecard"] for entry in block], [scorecard])
        self.assertAlmostEqual(block[0]["overall"], 105.263158, places=3)
        self.assertEqual(block[0]["perspectives"][0]["perspective"], "customer")

    def test_pack_bva_summary(self):
        # KPI-04: the budget vs actual summary of the month by center (cmc_budget reader)
        _lib, _scorecard, run = self._pack_run()
        row = next(row for row in run._cmc_pack_data()["bva"] if row["center_id"] == self.production)
        self.assertAlmostEqual(row["plan_month"], -1000.0, places=2)
        self.assertAlmostEqual(row["actual_month"], -1200.0, places=2)
        self.assertAlmostEqual(row["variance_month_pct"], -20.0, places=2)

    def test_pack_key_ratios(self):
        # KPI-04: key ratios of the year from the analysis used
        _lib, _scorecard, run = self._pack_run()
        ratio = next(row for row in run._cmc_pack_data()["ratios"] if row["code"] == "current_ratio")
        self.assertAlmostEqual(ratio["value"], 2.0, places=6)

    def test_pack_without_budget_or_analysis(self):
        # KPI-04: the BvA and ratio blocks are empty when the run names no source
        self._library()
        data = self._run(JAN)._cmc_pack_data()
        self.assertEqual((data["bva"], data["ratios"]), ([], []))

    def test_pdf_renders(self):
        # KPI-04: the printed pack shows KPI names, scorecard, BvA centers and ratio names (as the CMC user)
        lib, scorecard, run = self._pack_run()
        content, report_type = self.env["ir.actions.report"].with_user(self.user)._render_qweb_pdf(REPORT_XMLID, run.ids)
        self.assertEqual(report_type, "html")
        html = content.decode() if isinstance(content, bytes) else str(content)
        ratio_name = self.env["cmc.fin.ratio"].search([("code", "=", "current_ratio")], limit=1).name
        for text in (lib["otd"].name, lib["yield_pct"].name, scorecard.name, self.production.name, ratio_name):
            self.assertIn(str(escape(text)), html)
