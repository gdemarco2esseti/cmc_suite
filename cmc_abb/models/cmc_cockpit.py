from odoo import api, models


class CmcCockpit(models.TransientModel):
    _inherit = "cmc.cockpit"

    @api.model
    def _cmc_dashboard_cards(self, period):
        return super()._cmc_dashboard_cards(period) + self._cmc_dashboard_abb_card(period)

    @api.model
    def _cmc_dashboard_abb_card(self, period):
        Run = self.env["cmc.abb.run"]
        if not Run.has_access("read"):
            return []
        # why: what-if runs (baseline_run_id set) are scenarios, not the budget; same leaf as the action domain
        run = self._cmc_dashboard_latest_run(
            Run, ["name", "total_budgeted_cost", "total_assigned_cost", "total_unused_cost"], "id desc", [("baseline_run_id", "=", False)])
        figures = run and [
            self._cmc_dashboard_figure("abb.budgeted", self.env._("Budgeted cost"), run["total_budgeted_cost"], "monetary"),
            self._cmc_dashboard_figure("abb.assigned", self.env._("Assigned cost"), run["total_assigned_cost"], "monetary"),
            self._cmc_dashboard_figure("abb.unused", self.env._("Unused capacity cost"), run["total_unused_cost"], "monetary"),
        ]
        return [self._cmc_dashboard_card(
            "abb.run", "planning", 20, self.env._("Activity-based budgeting"), run, period, figures, "cmc_abb.action_cmc_abb_run")]
