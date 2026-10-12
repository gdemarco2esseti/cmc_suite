from odoo import fields, models


class AccountAccount(models.Model):
    _inherit = "account.account"

    # global mapping (account.account is company-shared in v19): financial account
    # -> cost-element nature, used to derive the element on analytic lines.
    cmc_cost_element_id = fields.Many2one(
        "cmc.cost.element",
        string="Cost Element (nature)",
        index=True,
        help="Maps this financial account to a cost-element nature; derived onto "
        "analytic lines posted on this account.",
    )
