from odoo import fields, models
from odoo.exceptions import UserError


class CmcScenarioRunMixin(models.AbstractModel):
    """Run of WP-11: header outputs written by Compute only, plan figures read through the version evaluator (AD-102)."""

    _name = "cmc.scenario.run.mixin"
    _inherit = ["cmc.run.mixin"]
    _description = "CMC scenario run"
    _check_company_auto = True

    # header outputs of the engine: written by Compute, cleared by Reset
    _scenario_outputs = ()

    name = fields.Char(required=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    evaluator_key = fields.Char(required=True, help="Key of the evaluator that turns a plan version into profit amounts.")

    def write(self, vals):
        if not self._cmc_state_unlocked() and not set(self._scenario_outputs).isdisjoint(vals):
            raise UserError(self.env._("The outcome of a run is written by Compute only."))
        return super().write(vals)

    def _cmc_reset_values(self):
        return {**super()._cmc_reset_values(), **dict.fromkeys(self._scenario_outputs, 0.0)}

    def _scenario_prepare(self, version):
        """The evaluator of the run for `version`: one batched read, then pure `evaluate(values)`."""
        return version._cmc_scenario_evaluator(self.evaluator_key)["prepare"](version)

    def _scenario_amounts(self, version):
        """{(center_id, element_id, month): amount in company currency} of `version` at its own inputs."""
        prepared = self._scenario_prepare(version)
        return {key: version._cmc_to_company_currency(amount) for key, amount in prepared["evaluate"](prepared["inputs"]).items()}

    @staticmethod
    def _scenario_sorted(keys):
        return sorted(keys, key=lambda key: (key[2], key[0] or 0, key[1] or 0))
