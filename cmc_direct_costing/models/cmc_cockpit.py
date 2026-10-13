from odoo import api, models


class CmcCockpit(models.TransientModel):
    _inherit = "cmc.cockpit"

    @api.model
    def _cmc_dashboard_cards(self, period):
        return (
            super()._cmc_dashboard_cards(period)
            + self._cmc_dashboard_cvr_card(period)
            + self._cmc_dashboard_direct_card(period)
            + self._cmc_dashboard_decision_card(period)
        )

    @api.model
    def _cmc_dashboard_kpis(self, period):
        return super()._cmc_dashboard_kpis(period) + self._cmc_dashboard_direct_kpi(period)

    @api.model
    def _cmc_dashboard_cvr_card(self, period):
        Cvr = self.env["cmc.cvr.analysis"]
        if not Cvr.has_access("read"):
            return []
        run = self._cmc_dashboard_latest_run(
            Cvr, ["name", "date_from", "date_to", "bep_revenue", "mos_ratio", "has_breakeven"], "date_to desc, id desc")
        figures = run and [
            self._cmc_dashboard_figure(
                "direct_costing.bep", self.env._("Break-even revenue"), run["bep_revenue"] if run["has_breakeven"] else None, "monetary"),
            self._cmc_dashboard_figure("direct_costing.mos", self.env._("Margin of safety"), run["mos_ratio"], "percent"),
        ]
        return [self._cmc_dashboard_card(
            "direct_costing.cvr", "decision", 20, self.env._("Cost-volume-profit"), run, period, figures,
            "cmc_direct_costing.action_cmc_cvr_analysis")]

    @api.model
    def _cmc_dashboard_direct_latest(self, period):
        def compute():
            Direct = self.env["cmc.direct.costing"]
            if not Direct.has_access("read"):
                return None
            return {"latest": self._cmc_dashboard_latest_run(
                Direct, ["name", "date_from", "date_to", "total_mc_semigross", "operating_result"], "date_to desc, id desc")}
        return self._cmc_dashboard_memo(period, "direct_costing.direct", compute)

    @api.model
    def _cmc_dashboard_direct_card(self, period):
        direct = self._cmc_dashboard_direct_latest(period)
        if direct is None:
            return []
        run = direct["latest"]
        figures = run and [
            self._cmc_dashboard_figure("direct_costing.mc", self.env._("Semi-gross margin"), run["total_mc_semigross"], "monetary"),
            self._cmc_dashboard_figure("direct_costing.result", self.env._("Operating result"), run["operating_result"], "monetary"),
        ]
        return [self._cmc_dashboard_card(
            "direct_costing.direct", "decision", 30, self.env._("Direct costing"), run, period, figures,
            "cmc_direct_costing.action_cmc_direct_costing")]

    @api.model
    def _cmc_dashboard_decision_card(self, period):
        Decision = self.env["cmc.decision"]
        if not Decision.has_access("read"):
            return []
        run = self._cmc_dashboard_latest_run(Decision, ["name", "date", "verdict", "differential"], "date desc, id desc")
        figures = run and [
            self._cmc_dashboard_figure("direct_costing.differential", self.env._("Differential"), run["differential"], "monetary"),
            self._cmc_dashboard_figure(
                "direct_costing.verdict", self.env._("Verdict"),
                dict(Decision._fields["verdict"]._description_selection(self.env)).get(run["verdict"]), "text"),
        ]
        return [self._cmc_dashboard_card(
            "direct_costing.decision", "decision", 40, self.env._("Decisions"), run, period, figures,
            "cmc_direct_costing.action_cmc_decision", date_to=run and run["date"])]

    @api.model
    def _cmc_dashboard_direct_kpi(self, period):
        direct = self._cmc_dashboard_direct_latest(period)
        if not direct or not direct["latest"]:
            return []  # unreadable or never any data: no tile, the card carries the empty state
        latest = direct["latest"]
        if not self._cmc_dashboard_in_period(latest["date_from"], latest["date_to"], period):
            # the latest by end date may start after the period: look for one that overlaps it
            Direct = self.env["cmc.direct.costing"]
            latest = self._cmc_dashboard_latest_run(
                Direct, ["date_from", "date_to", "total_mc_semigross", "operating_result"], "date_to desc, id desc",
                [("date_from", "<=", period["date_to"].isoformat()), ("date_to", ">=", period["date_from"].isoformat())])
        sub = self.env._("No data in this period") if not latest else self.env._(
            "Semi-gross margin %s", self._cmc_dashboard_money(latest["total_mc_semigross"], short=True))
        return [self._cmc_dashboard_kpi(
            "result", 20, "direct_costing.result", self.env._("Operating result"), latest and latest["operating_result"], "monetary",
            self.env._("from Direct Costing"), sub=sub)]
