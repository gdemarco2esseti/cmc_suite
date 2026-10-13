from odoo import api, models
from odoo.tools import float_is_zero

MODEL = "cmc.process.run"
ACTION = "cmc_process_costing.action_cmc_process_run"
CARD = "process_costing.run"


class CmcCockpit(models.TransientModel):
    _inherit = "cmc.cockpit"

    @api.model
    def _cmc_dashboard_process_run(self, period):
        # explicit order: the model's own _order differs
        return self._cmc_dashboard_memo(period, CARD, lambda: self._cmc_dashboard_latest_run(
            self.env[MODEL], ["name", "date_from", "date_to", "transferred_out_cost", "ending_wip_cost", "control_difference"],
            "date_to desc, id desc"))

    @api.model
    def _cmc_dashboard_cards(self, period):
        cards = super()._cmc_dashboard_cards(period)
        if self._cmc_dashboard_readable(MODEL):
            run = self._cmc_dashboard_process_run(period)
            figures = run and [
                self._cmc_dashboard_figure("process_costing.transferred", self.env._("Transferred out"), run["transferred_out_cost"], "monetary"),
                self._cmc_dashboard_figure("process_costing.wip", self.env._("Ending WIP"), run["ending_wip_cost"], "monetary"),
            ]
            cards.append(self._cmc_dashboard_card(
                CARD, "costing", 50, self.env._("Process costing"), run, period, figures or [], ACTION))
        return cards

    @api.model
    def _cmc_dashboard_alerts(self, period):
        alerts = super()._cmc_dashboard_alerts(period)
        run = self._cmc_dashboard_readable(MODEL) and self._cmc_dashboard_process_run(period)
        if run and not float_is_zero(run["control_difference"], precision_rounding=self.env.company.currency_id.rounding):
            alerts.append(self._cmc_dashboard_alert(
                "process_costing.control_difference", "danger",
                self.env._("Process control total off by %s", self._cmc_dashboard_money(run["control_difference"])),
                1, ACTION, [("id", "=", run["id"])], card=CARD, amount=run["control_difference"], scope="run"))
        return alerts
