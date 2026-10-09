from odoo import fields, models


class AccountAnalyticLine(models.Model):
    _inherit = "account.analytic.line"

    cmc_transfer_charge_id = fields.Many2one(
        "cmc.transfer.charge", string="Chargeback", index="btree_not_null", ondelete="restrict", readonly=True, copy=False,
        help="Chargeback run that posted this system line.",
    )
