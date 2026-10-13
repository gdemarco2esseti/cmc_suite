from odoo import api, models
from odoo.tools import float_is_zero

MODEL = "cmc.costing.run"
ACTION = "cmc_full_costing.action_cmc_costing_run"
CARD = "full_costing.run"


class CmcCockpit(models.TransientModel):
    _inherit = "cmc.cockpit"

    @api.model
    def _cmc_dashboard_full_costing_run(self, period):
        return self._cmc_dashboard_memo(period, CARD, lambda: self._cmc_dashboard_latest_run(
            self.env[MODEL], ["name", "date_from", "date_to", "unabsorbed_cost"], "date_to desc, id desc"))

    @api.model
    def _cmc_dashboard_cards(self, period):
        cards = super()._cmc_dashboard_cards(period)
        if self._cmc_dashboard_readable(MODEL):
            run = self._cmc_dashboard_full_costing_run(period)
            figures = run and [
                self._cmc_dashboard_figure("full_costing.unabsorbed", self.env._("Unabsorbed cost"), run["unabsorbed_cost"], "monetary"),
            ]
            cards.append(self._cmc_dashboard_card(
                CARD, "costing", 10, self.env._("Full costing"), run, period, figures or [], ACTION))
        return cards

    @api.model
    def _cmc_dashboard_alerts(self, period):
        alerts = super()._cmc_dashboard_alerts(period)
        run = self._cmc_dashboard_readable(MODEL) and self._cmc_dashboard_full_costing_run(period)
        if run and not float_is_zero(run["unabsorbed_cost"], precision_rounding=self.env.company.currency_id.rounding):
            alerts.append(self._cmc_dashboard_alert(
                "full_costing.unabsorbed", "warning",
                self.env._("Unabsorbed cost: %s", self._cmc_dashboard_money(run["unabsorbed_cost"])),
                1, ACTION, [("id", "=", run["id"])], card=CARD, amount=run["unabsorbed_cost"], scope="run"))
        return alerts
