from odoo import api, models


class CmcCockpit(models.TransientModel):
    _inherit = "cmc.cockpit"

    @api.model
    def _cmc_dashboard_cards(self, period):
        return super()._cmc_dashboard_cards(period) + self._cmc_dashboard_scenario_card(period)

    @api.model
    def _cmc_dashboard_scenario_card(self, period):
        Comparison, Simulation = self.env["cmc.scenario.comparison"], self.env["cmc.scenario.simulation"]
        if not Comparison.has_access("read"):
            return []
        comparison = self._cmc_dashboard_latest_run(Comparison, ["name", "delta_profit"], "id desc")
        simulation = Simulation.has_access("read") and self._cmc_dashboard_latest_run(Simulation, ["name", "p50_output"], "id desc")
        figures = []
        if comparison:
            figures.append(self._cmc_dashboard_figure(
                "scenario.delta_profit", self.env._("Profit delta"), comparison["delta_profit"], "monetary"))
        if simulation:
            figures.append(self._cmc_dashboard_figure("scenario.p50", self.env._("Simulated P50"), simulation["p50_output"], "monetary"))
        return [self._cmc_dashboard_card(
            "scenario.scenarios", "planning", 30, self.env._("Scenarios"), comparison or simulation or None, period, figures,
            "cmc_scenario.action_cmc_scenario_comparison")]
