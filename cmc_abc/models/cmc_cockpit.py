from odoo import api, models
from odoo.tools import float_is_zero

MODEL = "cmc.abc.run"
ACTION = "cmc_abc.action_cmc_abc_run"
CARD = "abc.run"
FIELDS = ["name", "date_from", "date_to", "total_object_cost", "sustaining_cost", "unused_capacity_cost", "unassigned_cost"]


class CmcCockpit(models.TransientModel):
    _inherit = "cmc.cockpit"

    @api.model
    def _cmc_dashboard_abc(self, period):
        """Latest done run + the latest done run inside the period (second query only when they differ)."""
        def compute():
            Run = self.env[MODEL]
            latest = self._cmc_dashboard_latest_run(Run, FIELDS, "id desc")
            in_period = latest
            # an undated run applies to every period
            if latest and (latest["date_from"] or latest["date_to"]) and not self._cmc_dashboard_in_period(
                    latest["date_from"], latest["date_to"], period):
                in_period = self._cmc_dashboard_latest_run(Run, FIELDS, "id desc", [
                    ("date_from", "<=", period["date_to"].isoformat()), ("date_to", ">=", period["date_from"].isoformat())])
            return {"latest": latest, "in_period": in_period}
        return self._cmc_dashboard_memo(period, CARD, compute)

    @api.model
    def _cmc_dashboard_kpis(self, period):
        kpis = super()._cmc_dashboard_kpis(period)
        abc = self._cmc_dashboard_readable(MODEL) and self._cmc_dashboard_abc(period)
        if abc and abc["latest"]:  # never any run: the tile is omitted, not a dash
            run = abc["in_period"]
            kpis.append(self._cmc_dashboard_kpi(
                "capacity", 20, "abc.capacity", self.env._("Unused capacity"), run and max(run["unused_capacity_cost"], 0.0), "monetary",
                self.env._("from ABC"), sub=None if run else self.env._("No data in this period")))
        return kpis

    @api.model
    def _cmc_dashboard_cards(self, period):
        cards = super()._cmc_dashboard_cards(period)
        if self._cmc_dashboard_readable(MODEL):
            run = self._cmc_dashboard_abc(period)["latest"]
            figures = run and [
                self._cmc_dashboard_figure("abc.object_cost", self.env._("Cost on objects"), run["total_object_cost"], "monetary"),
                self._cmc_dashboard_figure("abc.sustaining", self.env._("Sustaining cost"), run["sustaining_cost"], "monetary"),
            ]
            cards.append(self._cmc_dashboard_card(
                CARD, "costing", 20, self.env._("Activity-based costing"), run, period, figures or [], ACTION))
        return cards

    @api.model
    def _cmc_dashboard_alerts(self, period):
        alerts = super()._cmc_dashboard_alerts(period)
        run = self._cmc_dashboard_readable(MODEL) and self._cmc_dashboard_abc(period)["latest"]
        if run and run["unassigned_cost"] > 0 and not float_is_zero(
                run["unassigned_cost"], precision_rounding=self.env.company.currency_id.rounding):
            alerts.append(self._cmc_dashboard_alert(
                "abc.unassigned", "warning",
                self.env._("ABC cost not assigned: %s", self._cmc_dashboard_money(run["unassigned_cost"])),
                1, ACTION, [("id", "=", run["id"])], card=CARD, amount=run["unassigned_cost"], scope="run"))
        return alerts
