from datetime import date

from odoo import api, models


class CmcCockpit(models.TransientModel):
    _inherit = "cmc.cockpit"

    @api.model
    def _cmc_dashboard_fin(self, period):
        """Latest done analysis + ratio values of its last year by status (None when unreadable)."""
        def compute():
            Analysis, Ratio = self.env["cmc.fin.analysis"], self.env["cmc.fin.ratio.value"]
            if not Analysis.has_access("read"):
                return None
            run = self._cmc_dashboard_latest_run(Analysis, ["name", "year_to"], "year_to desc, id desc")
            counts, domain = None, []
            if run and Ratio.has_access("read"):
                domain = [("run_id", "=", run["id"]), ("year", "=", run["year_to"])] + self._cmc_dashboard_company_domain(Ratio)
                counts = dict(Ratio._read_group(domain, ["status"], ["__count"]))
            return {"run": run, "counts": counts, "domain": domain}
        return self._cmc_dashboard_memo(period, "fin_analysis.latest", compute)

    @api.model
    def _cmc_dashboard_cards(self, period):
        cards = super()._cmc_dashboard_cards(period)
        fin = self._cmc_dashboard_fin(period)
        if fin is None:
            return cards
        run, counts, figures = fin["run"], fin["counts"], []
        if counts is not None:
            figures = [
                self._cmc_dashboard_figure("fin_analysis.alert", self.env._("Ratios on alert"), counts.get("alert", 0), "count"),
                self._cmc_dashboard_figure("fin_analysis.ok", self.env._("Ratios OK"), counts.get("ok", 0), "count"),
            ]
        cards.append(self._cmc_dashboard_card(
            "fin_analysis.analysis", "financial", 10, self.env._("Financial analysis"), run, period, figures,
            "cmc_fin_analysis.action_cmc_fin_analysis", date_to=run and date(run["year_to"], 12, 31)))
        return cards

    @api.model
    def _cmc_dashboard_alerts(self, period):
        alerts = super()._cmc_dashboard_alerts(period)
        fin = self._cmc_dashboard_fin(period)
        count = fin and fin["counts"] and fin["counts"].get("alert", 0)
        if count:
            alerts.append(self._cmc_dashboard_alert(
                "fin_analysis.ratio_alert", "warning", self.env._("%s financial ratios on alert", count) if count > 1 else self.env._("1 financial ratio on alert"), count,
                "cmc_fin_analysis.action_cmc_fin_ratio_value", fin["domain"] + [("status", "=", "alert")]))
        return alerts
