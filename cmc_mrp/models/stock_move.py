from odoo import fields, models


class StockMove(models.Model):
    _inherit = "stock.move"

    cmc_mrp_analytic_line_ids = fields.Many2many(
        "account.analytic.line",
        "cmc_mrp_move_line_rel",
        "move_id",
        "line_id",
        string="CMC Material Cost Lines",
        copy=False,
        help="Management-control material cost lines posted from this "
        "consumption move; used to avoid double imputation.",
    )
