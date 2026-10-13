from odoo import api, models

MODEL = "cmc.standard.variance.run"
ACTION = "cmc_standard_cost.action_cmc_standard_variance_run"
CARD = "standard_cost.variance"


class CmcCockpit(models.TransientModel):
    _inherit = "cmc.cockpit"

    @api.model
    def _cmc_dashboard_standard_run(self, period):
        return self._cmc_dashboard_memo(period, CARD, lambda: self._cmc_dashboard_latest_run(
            self.env[MODEL], ["name", "date_from", "date_to", "variance_total", "skipped_count"], "date_to desc, id desc"))

    @api.model
    def _cmc_dashboard_cards(self, period):
        cards = super()._cmc_dashboard_cards(period)
        if self._cmc_dashboard_readable(MODEL):
            run = self._cmc_dashboard_standard_run(period)
            figures = run and [
                self._cmc_dashboard_figure("standard_cost.variance_total", self.env._("Total variance"), run["variance_total"], "monetary"),
            ]
            cards.append(self._cmc_dashboard_card(
                CARD, "costing", 60, self.env._("Standard cost variances"), run, period, figures or [], ACTION))
        return cards

    @api.model
    def _cmc_dashboard_alerts(self, period):
        alerts = super()._cmc_dashboard_alerts(period)
        run = self._cmc_dashboard_readable(MODEL) and self._cmc_dashboard_standard_run(period)
        if run and run["skipped_count"]:
            alerts.append(self._cmc_dashboard_alert(
                "standard_cost.skipped", "warning",
                self.env._("%s manufacturing orders without standard card", run["skipped_count"]) if run["skipped_count"] > 1
                else self.env._("1 manufacturing order without standard card"),
                run["skipped_count"], ACTION, [("id", "=", run["id"])], card=CARD, scope="run"))
        return alerts
