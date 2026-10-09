from odoo import models


class CmcPlanVersion(models.Model):
    _inherit = "cmc.plan.version"

    def _cmc_scenario_copy(self):
        self.ensure_one()
        return self.copy({"version_type": "simulation", "name": self.env._("%s (scenario)", self.name)})

    def action_cmc_create_scenario(self):
        scenario = self._cmc_scenario_copy()
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": scenario.id,
            "view_mode": "form",
            "target": "current",
        }
