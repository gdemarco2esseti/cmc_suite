from odoo import fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    tdabc_char_value_ids = fields.One2many(
        "cmc.tdabc.char.value", "partner_id",
        string="TDABC Characteristics",
        groups="cmc_base.group_cmc_user",
    )
