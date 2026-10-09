from odoo import fields, models
from odoo.exceptions import UserError

from odoo.addons.cmc_base.models.cmc_run_mixin import CMC_RUN_STATE


class AccountAnalyticLine(models.Model):
    _inherit = "account.analytic.line"

    cmc_transfer_charge_id = fields.Many2one(
        "cmc.transfer.charge", string="Chargeback", index="btree_not_null", ondelete="restrict", readonly=True, copy=False,
        help="Chargeback run that posted this system line.",
    )

    def _cmc_check_allocation_lock(self, vals_list=()):
        super()._cmc_check_allocation_lock(vals_list)
        # why no env.su bypass: same rule as allocation lines, only compute/reset (the sentinel) touch a posting
        if self.env.context.get("cmc_run_state") is CMC_RUN_STATE:
            return
        if any(vals.get("cmc_transfer_charge_id") for vals in vals_list) or self.filtered("cmc_transfer_charge_id"):
            raise UserError(self.env._("Lines posted by a chargeback change only by computing or resetting it."))
