"""BUD-04 / INT-07 - budget vs actual, live SQL view, monthly / YTD / prior year, % and alerts.

Hand-computed (center B, rent fixed, natural sign): budget Jan-Mar 1,000 each; actual 2033 Jan 900,
Feb 1,200, Mar 1,050; actual 2032 Jan 800, Feb 700, Mar 600.
  view Feb: plan -1,000, actual -1,200, variance -200 (unfavourable), prior -700; Jan variance +100.
  summary at 15 Feb: month plan -1,000 actual -1,200 var -200 (-20.0%); YTD plan -2,000 actual -2,100
  var -100 (-5.0%); prior YTD -1,500; threshold 10 -> alert_month True, alert_ytd False; threshold 4 -> both.
INT-07: budget 1,000 EUR at 1.1 -> plan -1,100 in company currency.
"""

from odoo.tests.common import tagged

from .common import CmcBudgetCase, d


@tagged("post_install", "-at_install")
class TestBudgetBva(CmcBudgetCase):
    """BUD-04: variance = actual - budget, absolute and %, monthly and YTD, against prior year, with alerts."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Bva = cls.env["cmc.budget.bva"]
        cls.center = cls._center("BUD bva B")
        cls.version = cls._version(name="BUD bva 2033")
        for month in (1, 2, 3):
            cls._line(cls.version, cls.center, month, 1000.0)
        for day, amount in ((d(1, 10), 900.0), (d(2, 10), 1200.0), (d(3, 10), 1050.0),
                            (d(1, 10, 2032), 800.0), (d(2, 10, 2032), 700.0), (d(3, 10, 2032), 600.0)):
            cls._cost(cls.center, amount, day)
        cls.version.with_user(cls.manager).action_approve()

    def _row(self, month, version=None, center=None):
        rows = self.Bva.search([("version_id", "=", (version or self.version).id), ("date", "=", d(month)),
                                ("center_id", "=", (center or self.center).id), ("kind", "=", "cost")])
        self.assertEqual(len(rows), 1, rows)
        return rows

    def test_monthly_row_plan_actual_variance(self):
        # BUD-04: one row per version x month x center x element x behavior
        self.assertRecordValues(self._row(2), [{
            "plan_amount": -1000.0, "actual_amount": -1200.0, "variance": -200.0, "prior_actual_amount": -700.0,
            "element_id": self.rent.id, "behavior": "fixed", "version_type": "budget", "company_id": self.company.id,
        }])

    def test_cost_saving_is_favourable(self):
        # AD-112: spending less than budget is a positive variance
        self.assertRecordValues(self._row(1), [{"actual_amount": -900.0, "variance": 100.0}])

    def test_view_is_live(self):
        # AD-114: the view reads the actuals live, no recompute
        self._cost(self.center, 300.0, d(3, 20))
        self.env.flush_all()
        self.assertRecordValues(self._row(3), [{"actual_amount": -1350.0, "variance": -350.0}])

    def test_actual_outside_span_is_not_a_row_of_the_version(self):
        # BUD-04: 2032 actuals are only the prior-year column of the 2033 version
        rows = self.Bva.search([("version_id", "=", self.version.id), ("center_id", "=", self.center.id)])
        self.assertEqual(sorted(set(rows.mapped("date"))), [d(1), d(2), d(3)])

    def test_unbudgeted_actual_shows_as_variance(self):
        # BUD-04: a cost without a budget line is still compared (plan 0)
        self._cost(self.center, 40.0, d(2, 5), element=self.energy, behavior="variable")
        self.env.flush_all()
        row = self.Bva.search([("version_id", "=", self.version.id), ("date", "=", d(2)), ("element_id", "=", self.energy.id)])
        self.assertRecordValues(row, [{"plan_amount": 0.0, "actual_amount": -40.0, "variance": -40.0}])

    def test_summary_month_ytd_and_prior_year(self):
        # BUD-04: monthly and YTD, absolute and %, against prior year
        (row,) = self.version._cmc_bva_summary(d(2, 15), groupby=("center_id",))
        self.assertEqual(row["center_id"], self.center)
        values = {k: round(v, 2) for k, v in row.items() if isinstance(v, float)}
        self.assertEqual(values, {
            "plan_month": -1000.0, "actual_month": -1200.0, "variance_month": -200.0, "variance_month_pct": -20.0,
            "plan_ytd": -2000.0, "actual_ytd": -2100.0, "variance_ytd": -100.0, "variance_ytd_pct": -5.0,
            "prior_ytd": -1500.0,
        })

    def test_alert_beyond_threshold(self):
        # BUD-04: alert when the variance % exceeds the version threshold
        (row,) = self.version._cmc_bva_summary(d(2, 15))
        self.assertEqual((row["alert_month"], row["alert_ytd"]), (True, False))
        self.version.alert_threshold = 4.0
        (row,) = self.version._cmc_bva_summary(d(2, 15))
        self.assertEqual((row["alert_month"], row["alert_ytd"]), (True, True))

    def test_bva_in_company_currency(self):
        # INT-07: plan figures are compared in company currency at the version's fixed rate
        eur = self.env.ref("base.EUR") if self.company.currency_id != self.env.ref("base.EUR") else self.env.ref("base.CHF")
        eur.active = True
        center = self._center("BUD bva fx")
        version = self._version(name="BUD bva fx", currency_id=eur.id, currency_rate=1.1)
        self._line(version, center, 3, 1000.0)
        self._cost(center, 1000.0, d(3, 3))
        self.env.flush_all()
        self.assertRecordValues(self._row(3, version=version, center=center),
                                [{"plan_amount": -1100.0, "actual_amount": -1000.0, "variance": 100.0}])

    def test_forecast_version_has_its_own_rows(self):
        # FCS-01: a forecast is comparable with the budget on the same actuals
        forecast = self.version.copy({"version_type": "forecast"})
        forecast.budget_line_ids.filtered(lambda line: line.date == d(2)).figure = 1200.0
        self.env.flush_all()
        self.assertRecordValues(self._row(2, version=forecast), [{"version_type": "forecast", "plan_amount": -1200.0, "variance": 0.0}])

    def test_summary_query_count_independent_of_volume(self):
        # AD-114: grouped reads; months, lines and postings do not add queries
        def build(prefix, months, postings):
            center = self._center(f"BUD bva qc {prefix}")
            version = self._version(name=f"BUD bva qc {prefix}")
            for month in range(1, months + 1):
                self._line(version, center, month, 100.0)
                for i in range(postings):
                    self._cost(center, 1.0 + i, d(month, 1 + i % 28))
            return version

        counts = []
        for version in (build("small", 1, 1), build("large", 12, 6)):
            self.env.invalidate_all()
            before = self.cr.sql_log_count
            version._cmc_bva_summary(d(12, 31))
            counts.append(self.cr.sql_log_count - before)
        self.assertLessEqual(counts[1], counts[0] + 2, f"query count grows with the volume: {counts}")
