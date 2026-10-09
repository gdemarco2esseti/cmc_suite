from odoo import models

from odoo.addons.cmc_account.models.account_analytic_line import CMC_GATE


class AccountAnalyticLine(models.Model):
    _inherit = "account.analytic.line"

    def _check_can_write(self, vals):
        # why: timesheet_grid asks env.user, which under the CMC gate's sudo is
        # still the controller, so a validated timesheet would refuse a write
        # that only touches classification fields. Only the gate sets the sentinel.
        if self.env.context.get("cmc_classification_gate") is CMC_GATE:
            return
        return super()._check_can_write(vals)
