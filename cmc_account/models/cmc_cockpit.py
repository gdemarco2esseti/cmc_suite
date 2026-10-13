from odoo import api, models
from odoo.tools import float_is_zero

MODEL = "cmc.coge.reconciliation"
ACTION = "cmc_account.action_cmc_coge_reconciliation"
CARD = "account.reconciliation"


class CmcCockpit(models.TransientModel):
    _inherit = "cmc.cockpit"

    @api.model
    def _cmc_dashboard_account_run(self, period):
        return self._cmc_dashboard_memo(period, CARD, lambda: self._cmc_dashboard_latest_run(
            self.env[MODEL], ["name", "date_from", "date_to", "coge_amount", "coan_amount", "unexplained_amount"],
            "date_to desc, id desc"))

    @api.model
    def _cmc_dashboard_cards(self, period):
        cards = super()._cmc_dashboard_cards(period)
        if self._cmc_dashboard_readable(MODEL):
            run = self._cmc_dashboard_account_run(period)
            figures = run and [
                self._cmc_dashboard_figure("account.coge", self.env._("General ledger cost"), run["coge_amount"], "monetary"),
                self._cmc_dashboard_figure("account.coan", self.env._("Management accounting cost"), run["coan_amount"], "monetary"),
                self._cmc_dashboard_figure("account.unexplained", self.env._("Unexplained"), run["unexplained_amount"], "monetary"),
            ]
            cards.append(self._cmc_dashboard_card(
                CARD, "recording", 20, self.env._("GL-CA reconciliation"), run, period, figures or [], ACTION))
        return cards

    @api.model
    def _cmc_dashboard_alerts(self, period):
        alerts = super()._cmc_dashboard_alerts(period)
        run = self._cmc_dashboard_readable(MODEL) and self._cmc_dashboard_account_run(period)
        if run and not float_is_zero(run["unexplained_amount"], precision_rounding=self.env.company.currency_id.rounding):
            alerts.append(self._cmc_dashboard_alert(
                "account.unexplained", "danger",
                self.env._("GL-CA difference not explained: %s", self._cmc_dashboard_money(run["unexplained_amount"])),
                1, ACTION, [("id", "=", run["id"])], card=CARD, amount=run["unexplained_amount"], scope="run"))
        return alerts
