from odoo import models

from odoo.addons.cmc_base.models.cmc_utils import month_start


class CmcPlanVersion(models.Model):
    _inherit = "cmc.plan.version"

    def _cmc_scenario_evaluators(self):
        entry = {"key": "cvr", "name": self.env._("Cost-volume-profit"), "prepare": self._cmc_cvr_scenario_prepare}
        return super()._cmc_scenario_evaluators() + [entry]

    def _cmc_cvr_scenario_prepare(self, version):
        """Inputs = price, volume, unit variable cost and fixed cost of a CVR analysis; the amount is its operating result."""
        # why: a CVR analysis is not tied to a version: the evaluator reads the latest one of the version company whose
        # period starts inside the version span (no match -> no input, profit 0)
        cvr = version.env["cmc.cvr.analysis"].search(
            [("company_id", "=", version.company_id.id), ("date_from", ">=", version.date_from), ("date_from", "<=", version.date_to)],
            order="date_to desc, id desc", limit=1,
        )
        key = (cvr.center_id.id or False, False, month_start(cvr.date_from)) if cvr else None
        inputs = (
            {"price": cvr.price_unit, "volume": cvr.volume, "variable_unit": cvr.variable_unit, "fixed_cost": cvr.fixed_cost}
            if cvr else {}
        )

        def evaluate(values):
            if key is None:
                return {}
            return {key: (values["price"] - values["variable_unit"]) * values["volume"] - values["fixed_cost"]}

        return {"inputs": inputs, "evaluate": evaluate}
