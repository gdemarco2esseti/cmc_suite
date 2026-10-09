from odoo import models

from odoo.addons.cmc_base.models.account_analytic_line import CMC_AUTO


class AccountMove(models.Model):
    _inherit = "account.move"

    def _reverse_moves(self, default_values_list=None, cancel=False):
        # why the sentinel: the reversal is built from copy_data, which carries
        # cmc_excluded; it is the system, not the user, that copies it over.
        reversals = super(AccountMove, self.with_context(cmc_auto_classify=CMC_AUTO))._reverse_moves(
            default_values_list, cancel=cancel
        )
        return reversals.with_env(self.env)
