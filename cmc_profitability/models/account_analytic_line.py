from odoo import api, fields, models


class AccountAnalyticLine(models.Model):
    _inherit = "account.analytic.line"

    cmc_transfer_charge_id = fields.Many2one(
        "cmc.transfer.charge", string="Chargeback", index="btree_not_null", ondelete="restrict", readonly=True, copy=False,
        help="Chargeback run that posted this system line.",
    )

    _cmc_transfer_charge_requires_relocation = models.Constraint(
        "CHECK(cmc_transfer_charge_id IS NULL OR cmc_is_relocation IS TRUE)",
        "A line linked to a chargeback run must be a relocation posting.",
    )

    @api.model
    def _cmc_relocation_link_fields(self):
        return (*super()._cmc_relocation_link_fields(), "cmc_transfer_charge_id")
