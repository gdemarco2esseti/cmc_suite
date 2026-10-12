from odoo import fields, models

from .cmc_standard_mixin import PRECISE


class CmcStandardPrice(models.Model):
    _name = "cmc.standard.price"
    _inherit = ["cmc.standard.line.mixin"]
    _description = "Standard Purchase Price"
    _order = "version_id, product_id"

    product_id = fields.Many2one("product.product", required=True, check_company=True, ondelete="restrict")
    price = fields.Float(digits=PRECISE, help="Planned purchase price per unit of measure of the product.")

    _version_product_uniq = models.UniqueIndex("(version_id, product_id)", "One planned price per product in a version.")
