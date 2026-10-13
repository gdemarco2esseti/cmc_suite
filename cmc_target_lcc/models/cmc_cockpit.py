from odoo import api, models


class CmcCockpit(models.TransientModel):
    _inherit = "cmc.cockpit"

    @api.model
    def _cmc_dashboard_target_lcc(self, period):
        """Latest done target cost + latest done LCC model (None when the target costs are unreadable)."""
        def compute():
            Target, LCC = self.env["cmc.target.cost"], self.env["cmc.lcc.model"]
            if not Target.has_access("read"):
                return None
            target = self._cmc_dashboard_latest_run(Target, ["name", "date", "cost_gap"], "date desc, id desc")
            lcc = self._cmc_dashboard_latest_run(LCC, ["lc_profit_npv"], "id desc") if target and LCC.has_access("read") else None
            return {"target": target, "lcc": lcc, "lcc_readable": LCC.has_access("read")}
        return self._cmc_dashboard_memo(period, "target_lcc.latest", compute)

    @api.model
    def _cmc_dashboard_cards(self, period):
        cards = super()._cmc_dashboard_cards(period)
        latest = self._cmc_dashboard_target_lcc(period)
        if latest is None:
            return cards
        target, figures = latest["target"], []
        if target:
            figures.append(self._cmc_dashboard_figure("target_lcc.cost_gap", self.env._("Cost gap"), target["cost_gap"], "monetary"))
            if latest["lcc_readable"]:
                figures.append(self._cmc_dashboard_figure(
                    "target_lcc.lcc_npv", self.env._("Life-cycle profit (NPV)"), latest["lcc"] and latest["lcc"]["lc_profit_npv"], "monetary"))
        cards.append(self._cmc_dashboard_card(
            "target_lcc.target", "planning", 40, self.env._("Target & life-cycle costing"), target, period, figures,
            "cmc_target_lcc.action_cmc_target_cost", date_to=target and target["date"]))
        return cards

    @api.model
    def _cmc_dashboard_alerts(self, period):
        alerts = super()._cmc_dashboard_alerts(period)
        Target = self.env["cmc.target.cost"]
        if not Target.has_access("read"):
            return alerts
        domain = self._cmc_dashboard_company_domain(Target) + self._cmc_dashboard_period_domain(period) + [
            ("state", "=", "done"), ("target_met", "=", False)]
        count = Target.search_count(domain)
        if count:
            alerts.append(self._cmc_dashboard_alert(
                "target_lcc.above_target", "info", self.env._("%s products above target cost", count) if count > 1 else self.env._("1 product above target cost"), count,
                "cmc_target_lcc.action_cmc_target_cost", domain))
        return alerts
