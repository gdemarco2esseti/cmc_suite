from odoo import fields, models


class CmcRate(models.Model):
    _inherit = "cmc.rate"

    abb_run_id = fields.Many2one("cmc.abb.run", string="ABB Run", ondelete="cascade", index=True, copy=False)
