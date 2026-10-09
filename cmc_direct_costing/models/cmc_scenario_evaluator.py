from odoo import models

from odoo.addons.cmc_base.models.cmc_utils import month_start


class CmcPlanVersion(models.Model):
    _inherit = "cmc.plan.version"

    def _cmc_scenario_evaluators(self):
        entry = {"key": "cvr", "name": self.env._("Cost-volume-profit"), "versioned": False, "prepare": self._cmc_cvr_scenario_prepare}
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
        # why: the figures are company-currency; the engine multiplies by currency_rate, so monetary inputs are divided here
        rate = version.currency_rate or 1.0
        inputs, mix = {}, ()
        if cvr:
            inputs["fixed_cost"] = cvr.fixed_cost / rate
            if cvr.is_multiproduct:
                mix = tuple(cvr.line_ids.ids)
                for line in cvr.line_ids:
                    inputs[f"line/{line.id}/price"] = line.price_unit / rate
                    inputs[f"line/{line.id}/variable_unit"] = line.variable_unit / rate
                    inputs[f"line/{line.id}/qty"] = line.qty
            else:
                inputs.update(price=cvr.price_unit / rate, volume=cvr.volume, variable_unit=cvr.variable_unit / rate)

        def evaluate(values):
            if key is None:
                return {}
            if mix:
                margin = sum(
                    values[f"line/{i}/qty"] * (values[f"line/{i}/price"] - values[f"line/{i}/variable_unit"]) for i in mix
                )
            else:
                margin = (values["price"] - values["variable_unit"]) * values["volume"]
            return {key: margin - values["fixed_cost"]}

        return {"inputs": inputs, "evaluate": evaluate}
