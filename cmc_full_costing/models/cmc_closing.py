from odoo import models
from odoo.exceptions import UserError


class CmcClosingRun(models.Model):
    _inherit = "cmc.closing.run"

    def _cmc_check_no_dependents(self):
        """AD-002: a computed costing run reads the final-center residuals of
        its closing run. Reopening (or deleting) that closing would leave the
        costing certified on numbers that no longer exist: reset the costing
        first. No cascade."""
        super()._cmc_check_no_dependents()
        # why: the dependency must be seen whole even when the user cannot
        # read every costing run of the company; only names are reported
        costings = self.env["cmc.costing.run"].sudo().search(
            [("closing_run_id", "in", self.ids), ("state", "=", "done")]
        )
        if costings:
            raise UserError(
                self.env._(
                    "Closing run %(closing)s is used by the computed costing runs "
                    "%(costings)s. Reset them to draft first.",
                    closing=", ".join(costings.closing_run_id.mapped("display_name")),
                    costings=", ".join(costings.mapped("display_name")),
                )
            )
