from odoo import api, models

MODEL = "cmc.joint.run"
ACTION = "cmc_joint_costs.action_cmc_joint_run"
CARD = "joint_costs.run"


class CmcCockpit(models.TransientModel):
    _inherit = "cmc.cockpit"

    @api.model
    def _cmc_dashboard_joint_run(self, period):
        return self._cmc_dashboard_memo(period, CARD, lambda: self._cmc_dashboard_latest_run(
            self.env[MODEL], ["name", "date_from", "date_to", "joint_cost", "method"], "id desc"))

    @api.model
    def _cmc_dashboard_cards(self, period):
        cards = super()._cmc_dashboard_cards(period)
        if self._cmc_dashboard_readable(MODEL):
            run = self._cmc_dashboard_joint_run(period)
            figures = []
            if run:
                methods = dict(self.env[MODEL]._fields["method"]._description_selection(self.env))
                figures = [
                    self._cmc_dashboard_figure("joint_costs.joint_cost", self.env._("Joint cost"), run["joint_cost"], "monetary"),
                    self._cmc_dashboard_figure("joint_costs.method", self.env._("Method"), methods.get(run["method"], run["method"]), "text"),
                ]
            cards.append(self._cmc_dashboard_card(
                CARD, "costing", 30, self.env._("Joint costs"), run, period, figures, ACTION))
        return cards
