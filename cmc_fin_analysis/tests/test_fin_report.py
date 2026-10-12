"""WP-13 report: ``cmc.fin.analysis._fin_report_data()`` shape and the QWeb PDF of the analysis.

CONTRACT: see ``tests/common.py`` (section "Extras": ``_fin_report_data`` shape and report xmlid).
The report data is read and the PDF printed AS the CMC user (``with_user``). In test mode ``_render_qweb_pdf`` falls back to the
HTML rendering (no wkhtmltopdf), which is what is asserted: the template renders and prints the data.
"""

from markupsafe import escape

from odoo.tests import tagged

from .common import FinAnalysisCommon

REPORT_XMLID = "cmc_fin_analysis.action_report_cmc_fin_analysis"
LINE_KEYS = {"code", "name", "line_type", "values", "common_size", "breaks"}


@tagged("post_install", "-at_install")
class TestFinReport(FinAnalysisCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.analysis = cls._run(2024, 2026, ateco_code="25.62")
        cls.analysis.action_compute()

    def _data(self, run=None):
        return (run or self.analysis).with_user(self.cmc_user)._fin_report_data()

    def _block(self, data, xmlid):
        scheme = self.env.ref("cmc_fin_analysis.%s" % xmlid)
        blocks = [block for block in data["schemes"] if block["scheme"] == scheme]
        self.assertEqual(len(blocks), 1, xmlid)
        return blocks[0]

    def _report_line(self, block, code):
        lines = [line for line in block["lines"] if line["code"] == code]
        self.assertEqual(len(lines), 1, code)
        return lines[0]

    # ---- _fin_report_data shape ---------------------------------------------------------------------

    def test_report_data_years_and_scheme_order(self):
        # FIN-05: multi-year table, one block per run scheme in scheme order
        data = self._data()
        self.assertEqual(set(data), {"years", "schemes", "ratios"})
        self.assertEqual(data["years"], [2024, 2025, 2026])
        expected = self.analysis.scheme_ids.sorted(lambda scheme: (scheme.sequence, scheme.id))
        self.assertEqual([block["scheme"] for block in data["schemes"]], list(expected))

    def test_report_data_scheme_lines(self):
        # FIN-01 / FIN-05: lines in sequence order with values and common-size per year
        data = self._data()
        bs = self._block(data, "scheme_bs_functional")
        scheme = self.schemes["scheme_bs_functional"]
        self.assertEqual([line["code"] for line in bs["lines"]],
                         scheme.line_ids.sorted(lambda line: (line.sequence, line.id)).mapped("code"))
        total = self._report_line(bs, "total_assets")
        self.assertEqual(set(total), LINE_KEYS)
        self.assertEqual(total["name"], self._line_rec("scheme_bs_functional", "total_assets").name)
        self.assertEqual(total["line_type"], "formula")
        self.assertEqual(set(total["values"]), {2024, 2025, 2026})
        for year, value in {2024: 430000, 2025: 488000, 2026: 557000}.items():
            self.assertAlmostEqual(total["values"][year], value, places=2)
            self.assertAlmostEqual(total["common_size"][year], 100.0, places=4)
        receivables = self._report_line(bs, "receivables")
        self.assertEqual(receivables["line_type"], "account")
        self.assertAlmostEqual(receivables["common_size"][2026], 150000 / 557000 * 100, places=4)

    def test_report_data_missing_value_is_absent_key(self):
        # FIN-09: the first year has no cash flow (no previous balance sheet) -> key absent, printed "n.c."
        cf = self._block(self._data(), "scheme_cf_functional")
        fco = self._report_line(cf, "cf_fco")
        self.assertNotIn(2024, fco["values"])
        self.assertAlmostEqual(fco["values"][2026], 107000, places=2)

    def test_report_data_comparability_breaks(self):
        # FIN-05: the valuation-criteria change is carried to the report for the flagged year only
        self.env["cmc.fin.criteria.change"].create({
            "company_id": self.company.id, "year": 2025, "name": "Finished goods LIFO -> FIFO", "line_codes": "inventory",
        })
        run = self._run(2024, 2026)
        run.action_compute()
        bs = self._block(self._data(run), "scheme_bs_functional")
        self.assertEqual(self._report_line(bs, "inventory")["breaks"], [2025])
        self.assertEqual(self._report_line(bs, "receivables")["breaks"], [])

    def test_report_data_ratios_by_area(self):
        # FIN-06..FIN-11: ratios grouped by area (Selection order), status and sector benchmark per year
        data = self._data()
        selection = self.env["cmc.fin.ratio"]._fields["area"]._description_selection(self.env)
        order = [key for key, _label in selection]
        areas = [group["area"] for group in data["ratios"]]
        self.assertEqual(areas, sorted(areas, key=order.index))
        self.assertLessEqual({"liquidity", "solidity", "profitability", "cash_flow", "value", "crisis", "data_quality"}, set(areas))
        labels = dict(selection)
        for group in data["ratios"]:
            self.assertEqual(group["area_label"], labels[group["area"]])
        profitability = next(group for group in data["ratios"] if group["area"] == "profitability")
        roe = next(row for row in profitability["rows"] if row["code"] == "roe")
        self.assertEqual(set(roe), {"code", "name", "unit", "values"})
        self.assertEqual(roe["name"], self._ratio_def("roe").name)
        self.assertEqual(roe["values"][2026]["status"], "ok")
        self.assertAlmostEqual(roe["values"][2026]["value"], 116000 / 274000, places=6)
        self.assertAlmostEqual(roe["values"][2026]["benchmark"], 0.15)
        self.assertIsNone(roe["values"][2025]["benchmark"])
        value = next(group for group in data["ratios"] if group["area"] == "value")
        wacc = next(row for row in value["rows"] if row["code"] == "wacc")
        self.assertEqual(wacc["values"][2025]["status"], "not_computable")

    def test_report_data_query_count_independent_of_years(self):
        # AD-114: built from the run's own rows in batch; a 3-year run costs the same queries as a 1-year run
        small_run = self._run(2026, 2026, ateco_code="25.62")
        small_run.action_compute()

        def queries(run):
            self.env.flush_all()
            self.env.invalidate_all()
            before = self.env.cr.sql_log_count
            self._data(run)
            return self.env.cr.sql_log_count - before

        queries(small_run)  # warm-up (ormcache, translations)
        small = queries(small_run)
        big = queries(self.analysis)
        self.assertGreater(len(self.analysis.value_ids), 2 * len(small_run.value_ids))
        self.assertEqual(big, small)

    # ---- QWeb PDF ------------------------------------------------------------------------------------

    def test_report_action_bound_to_analysis(self):
        # FIN-01..FIN-11: the analysis prints from its own form (Print menu)
        report = self.env.ref(REPORT_XMLID)
        self.assertRecordValues(report, [{"model": "cmc.fin.analysis", "report_type": "qweb-pdf"}])
        self.assertEqual(report.binding_model_id.model, "cmc.fin.analysis")

    def test_report_pdf_renders_lines_years_and_ratios(self):
        # FIN-01..FIN-11: the CMC user prints the computed analysis; the document carries the key rows
        content, report_type = self.env["ir.actions.report"].with_user(self.cmc_user)._render_qweb_pdf(REPORT_XMLID, self.analysis.ids)
        self.assertEqual(report_type, "html")  # test mode fallback, see module docstring
        html = content.decode() if isinstance(content, bytes) else str(content)
        for year in ("2024", "2025", "2026"):
            self.assertIn(year, html)
        names = [
            self._line_rec("scheme_bs_functional", "total_assets").name,
            self._line_rec("scheme_bs_functional", "coin").name,
            self._line_rec("scheme_pl_value_added", "value_added").name,
            self._line_rec("scheme_cf_liquidity", "liq_residual").name,
            self._ratio_def("roe").name,
        ]
        for name in names:
            self.assertIn(str(escape(name)), html)
        selection = dict(self.env["cmc.fin.ratio"]._fields["area"]._description_selection(self.env))
        self.assertIn(str(escape(selection["data_quality"])), html)
        self.assertIn("n.c.", html)

    def _line_rec(self, xmlid, code):
        return self._line(self.env.ref("cmc_fin_analysis.%s" % xmlid), code)
