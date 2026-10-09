from odoo import fields, models
from odoo.exceptions import UserError

from odoo.addons.cmc_base.models.cmc_run_mixin import CMC_RUN_STATE


class CmcBudgetLine(models.Model):
    _inherit = "cmc.budget.line"

    abb_run_id = fields.Many2one("cmc.abb.run", string="ABB Run", ondelete="cascade", index=True, copy=False)
    abb_resource_id = fields.Many2one("cmc.abc.resource", string="ABB Resource", copy=False)

    def _cmc_check_abb_output(self):
        # why sudo: the lock must hold whatever the user's rules show
        if self.env.context.get("cmc_run_state") is not CMC_RUN_STATE and self.sudo().abb_run_id.filtered(lambda run: run.state == "done"):
            raise UserError(self.env._("Budget lines written by an ABB run change only by resetting that run."))

    def write(self, vals):
        self._cmc_check_abb_output()
        return super().write(vals)

    def unlink(self):
        self._cmc_check_abb_output()
        return super().unlink()
