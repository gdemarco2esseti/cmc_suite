from odoo import fields, models


class CmcCostElement(models.Model):
    _inherit = "cmc.cost.element"

    cmc_cash_excluded = fields.Boolean(
        string="Excluded from Cash Forecast",
        help="A cost that is not a payment (e.g. depreciation): the budget cash forecast skips it.",
    )
