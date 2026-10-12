from odoo import api, fields, models
from odoo.exceptions import ValidationError

from .cmc_fin_utils import check_expression


class CmcFinRatio(models.Model):
    _name = "cmc.fin.ratio"
    _description = "Financial Analysis Ratio"
    _order = "sequence, id"
    _check_company_auto = True

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    area = fields.Selection(
        [
            ("liquidity", "Liquidity"),
            ("solidity", "Solidity"),
            ("profitability", "Profitability"),
            ("cash_flow", "Cash flow"),
            ("crisis", "Crisis indicators"),
            ("value", "Value creation"),
            ("data_quality", "Data quality"),
        ],
        required=True,
    )
    expression = fields.Char(required=True)
    unit = fields.Selection(
        [("ratio", "Ratio"), ("percent", "Percent"), ("days", "Days"), ("amount", "Amount")], required=True, default="ratio"
    )
    threshold_op = fields.Selection([(">", ">"), (">=", ">="), ("<", "<"), ("<=", "<=")])
    threshold = fields.Float()
    full_only = fields.Boolean(help="Needs detail not available in the abbreviated (reduced) statements.")
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company, index=True)
    active = fields.Boolean(default=True)

    _unique_code = models.UniqueIndex("(code, COALESCE(company_id, 0))", "The ratio code must be unique per company.")

    @api.constrains("code", "expression")
    def _check_code_and_expression(self):
        for ratio in self:
            if not (ratio.code or "").isidentifier():
                raise ValidationError(self.env._("The ratio code %s must be a valid identifier.", ratio.code))
            check_expression(self.env, ratio.expression, ratio.code)
