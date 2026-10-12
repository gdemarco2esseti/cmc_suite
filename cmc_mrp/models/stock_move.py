from odoo import fields, models


class StockMove(models.Model):
    _inherit = "stock.move"

    # The relation is 1:N move -> lines, so it is stored as a Many2one on the
    # analytic line (cmc_mrp_move_id) and set inside the create vals: no extra
    # write per move, no reliance on create() returning records in vals order,
    # and the idempotency guard becomes one indexed lookup.
    cmc_mrp_analytic_line_ids = fields.One2many(
        "account.analytic.line",
        "cmc_mrp_move_id",
        string="CMC Material Cost Lines",
        help="Management-control material cost lines posted from this "
        "consumption move; used to avoid double imputation.",
    )
