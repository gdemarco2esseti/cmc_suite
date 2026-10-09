from odoo import api, fields, models
from odoo.exceptions import ValidationError

from odoo.addons.cmc_account.models.account_move_line import REVENUE_TYPES


class CmcProfitPriceElement(models.Model):
    _name = "cmc.profit.price.element"
    _description = "Price Waterfall Element"
    _order = "sequence, id"
    _check_company_auto = True
    _check_company_domain = models.check_company_domain_parent_of

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    account_ids = fields.Many2many(
        "account.account", string="Accounts", check_company=True,
        help="Off-invoice items (freight, rebates, bonuses) between the invoice price and the pocket price.",
    )

    _code_company_uniq = models.Constraint("UNIQUE(code, company_id)", "The element code must be unique per company.")

    @api.constrains("account_ids")
    def _check_account_ids(self):
        for element in self:
            if element.account_ids.filtered(lambda account: account.account_type in REVENUE_TYPES):
                raise ValidationError(self.env._("An income account is already part of the revenue: it cannot be a price element."))
