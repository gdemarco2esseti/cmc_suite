from odoo import api, models
from odoo.tools import float_compare

RUN_FIELDS = ["name", "date_from", "date_to", "revenue_total", "contribution_total", "operating_profit", "unassigned_cost"]
RUN_ORDER = "date_to desc, id desc"


class CmcCockpit(models.TransientModel):
    _inherit = "cmc.cockpit"

    @api.model
    def _cmc_dashboard_profit_latest(self, period):
        """Latest done profit run, whatever its dates (card, alert); None when unreadable."""
        def compute():
            Run = self.env["cmc.profit.run"]
            return {"run": self._cmc_dashboard_latest_run(Run, RUN_FIELDS, RUN_ORDER)} if Run.has_access("read") else None
        return self._cmc_dashboard_memo(period, "profitability.latest", compute)

    @api.model
    def _cmc_dashboard_profit_in_period(self, period):
        """Latest done run overlapping the period (KPI): the overall latest when it overlaps, else one more query."""
        def compute():
            latest = self._cmc_dashboard_profit_latest(period)["run"]
            if not latest or self._cmc_dashboard_in_period(latest["date_from"], latest["date_to"], period):
                return latest
            return self._cmc_dashboard_latest_run(
                self.env["cmc.profit.run"], RUN_FIELDS, RUN_ORDER,
                [("date_from", "<=", period["date_to"].isoformat()), ("date_to", ">=", period["date_from"].isoformat())])
        return self._cmc_dashboard_memo(period, "profitability.in_period", compute)

    @api.model
    def _cmc_dashboard_kpis(self, period):
        kpis = super()._cmc_dashboard_kpis(period)
        latest = self._cmc_dashboard_profit_latest(period)
        if not latest or not latest["run"]:
            return kpis  # unreadable or never computed: the slot is left to the other contributors
        run = self._cmc_dashboard_profit_in_period(period)
        sub = self.env._("No data in this period")
        if run:
            margin = run["operating_profit"] / run["revenue_total"] * 100 if run["revenue_total"] else None
            sub = self.env._(
                "Revenue %(revenue)s · Margin %(margin)s",
                revenue=self._cmc_dashboard_money(run["revenue_total"], short=True),
                margin=self._cmc_dashboard_format(margin, "percent"))
        kpis.append(self._cmc_dashboard_kpi(
            "result", 10, "profitability.result", self.env._("Operating result"), run and run["operating_profit"], "monetary",
            self.env._("from Profitability"), sub=sub))
        return kpis

    @api.model
    def _cmc_dashboard_cards(self, period):
        cards = super()._cmc_dashboard_cards(period)
        latest = self._cmc_dashboard_profit_latest(period)
        if latest is None:
            return cards
        run = latest["run"]
        figures = run and [
            self._cmc_dashboard_figure("profitability.revenue", self.env._("Revenue"), run["revenue_total"], "monetary"),
            self._cmc_dashboard_figure("profitability.contribution", self.env._("Contribution"), run["contribution_total"], "monetary"),
        ]
        cards.append(self._cmc_dashboard_card(
            "profitability.run", "decision", 50, self.env._("Profitability"), run, period, figures, "cmc_profitability.action_cmc_profit_run"))
        return cards

    @api.model
    def _cmc_dashboard_alerts(self, period):
        alerts = super()._cmc_dashboard_alerts(period)
        run = (self._cmc_dashboard_profit_latest(period) or {}).get("run")
        if run and float_compare(run["unassigned_cost"], 0.0, precision_rounding=self.env.company.currency_id.rounding) > 0:
            alerts.append(self._cmc_dashboard_alert(
                "profitability.unassigned", "warning",
                self.env._("Unassigned costs (profitability): %s", self._cmc_dashboard_money(run["unassigned_cost"])),
                1, "cmc_profitability.action_cmc_profit_run", [("id", "=", run["id"])],
                card="profitability.run", amount=run["unassigned_cost"], scope="run"))
        return alerts
