from odoo import fields, models


class CmcStdPushLine(models.Model):
    _name = "cmc.std.push.line"
    _description = "Pushed Standard Price"
    _order = "version_id, product_id"

    version_id = fields.Many2one("cmc.plan.version", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="version_id.company_id", store=True, index=True)
    product_id = fields.Many2one("product.product")
    old_cost = fields.Float(digits="Product Price")
    new_cost = fields.Float(digits="Product Price")
