from odoo import fields, models


class ProductTemplate(models.Model):
    _inherit = "product.template"

    tdabc_char_value_ids = fields.One2many(
        "cmc.tdabc.char.value", "product_tmpl_id",
        string="TDABC Characteristics",
    )
