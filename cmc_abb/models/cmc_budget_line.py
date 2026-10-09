from odoo import fields, models


class CmcBudgetLine(models.Model):
    _inherit = "cmc.budget.line"

    abb_run_id = fields.Many2one("cmc.abb.run", string="ABB Run", ondelete="cascade", index=True, copy=False)
    abb_resource_id = fields.Many2one("cmc.abc.resource", string="ABB Resource", copy=False)
