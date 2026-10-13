from odoo import api, models

SCORE_FIELDS = ["score", "date"]


class CmcCockpit(models.TransientModel):
    _inherit = "cmc.cockpit"

    @api.model
    def _cmc_dashboard_kpi_scores(self, period):
        """Overall scores (newest first, at most two) of done runs up to the period end; None when unreadable."""
        def compute():
            Result = self.env["cmc.kpi.scorecard.result"]
            # why: the domain traverses run_id.state, so the run must be readable too
            if not self._cmc_dashboard_readable(Result, "cmc.kpi.run"):
                return None
            domain = [("perspective", "=", False), ("run_id.state", "=", "done")] + self._cmc_dashboard_company_domain(Result)
            # month-dated readings: the running month is not closed yet
            cut = self._cmc_dashboard_closed_months_cut(period)
            upper = [("date", "<", cut.isoformat())] if cut else [("date", "<=", period["date_to"].isoformat())] if period["date_to"] else []
            rows = Result.search_read(
                domain + upper,
                SCORE_FIELDS, limit=2, order="date desc, id desc")
            # a result before the period end exists or not: only then the tile shows a dash instead of vanishing
            return {"rows": rows, "ever": bool(rows) or bool(Result.search_count(domain, limit=1))}
        return self._cmc_dashboard_memo(period, "kpi.scores", compute)

    @api.model
    def _cmc_dashboard_kpi_values(self, period):
        """KPI measurements of done runs in the period by status (None when unreadable)."""
        def compute():
            Value = self.env["cmc.kpi.value"]
            if not self._cmc_dashboard_readable(Value, "cmc.kpi.run"):
                return None
            domain = [("run_id.state", "=", "done")] + self._cmc_dashboard_company_domain(Value) + self._cmc_dashboard_period_domain(period, closed_months=True)
            groups = Value._read_group(domain, ["status"], ["__count", "date:max"])
            return {"counts": {status: count for status, count, _last in groups},
                    "last": max((last for _s, _c, last in groups), default=None), "domain": domain}
        return self._cmc_dashboard_memo(period, "kpi.values", compute)

    @api.model
    def _cmc_dashboard_kpis(self, period):
        kpis = super()._cmc_dashboard_kpis(period)
        scores = self._cmc_dashboard_kpi_scores(period)
        if not scores or not scores["ever"]:
            return kpis
        rows, trend = scores["rows"], None
        if len(rows) == 2:
            delta = round(rows[0]["score"] - rows[1]["score"], 1)
            trend = {
                "text": self.env._("%(delta)s pts vs previous", delta=("+" if delta > 0 else "") + self._cmc_dashboard_format(delta, "ratio")),
                "direction": "up" if delta > 0 else "down" if delta < 0 else "flat",
                "favourable": delta >= 0,
            }
        kpis.append(self._cmc_dashboard_kpi(
            "scorecard", 10, "kpi.scorecard", self.env._("Scorecard"), rows[0]["score"] if rows else None, "ratio",
            self.env._("from Balanced Scorecard"), sub=None if rows else self.env._("No data in this period"), trend=trend))
        return kpis

    @api.model
    def _cmc_dashboard_cards(self, period):
        cards = super()._cmc_dashboard_cards(period)
        scores = self._cmc_dashboard_kpi_scores(period)
        if scores is None:
            return cards
        values = self._cmc_dashboard_kpi_values(period)
        counts = values and values["counts"]
        score = scores["rows"][0] if scores["rows"] else None
        last = max(filter(None, [score and score["date"], values and values["last"]]), default=None)
        figures = []
        if values:
            figures += [
                self._cmc_dashboard_figure("kpi.green", self.env._("On target"), counts.get("green", 0), "count"),
                self._cmc_dashboard_figure("kpi.amber", self.env._("Attention"), counts.get("amber", 0), "count"),
                self._cmc_dashboard_figure("kpi.red", self.env._("Off target"), counts.get("red", 0), "count", "danger" if counts.get("red") else "info"),
            ]
        cards.append(self._cmc_dashboard_card(
            "kpi.scorecard", "performance", 10, self.env._("Balanced scorecard"), last and {"date_to": last}, period, figures,
            "cmc_kpi.action_cmc_kpi_scorecard_result"))
        return cards

    @api.model
    def _cmc_dashboard_alerts(self, period):
        alerts = super()._cmc_dashboard_alerts(period)
        values = self._cmc_dashboard_kpi_values(period)
        count = values and values["counts"].get("red", 0)
        if count:
            alerts.append(self._cmc_dashboard_alert(
                "kpi.red", "danger", self.env._("%s KPIs in red", count) if count > 1 else self.env._("1 KPI in red"), count, "cmc_kpi.action_cmc_kpi_value",
                values["domain"] + [("status", "=", "red")], card="kpi.scorecard"))
        return alerts
