from odoo import api, fields, models

from .cmc_standard_mixin import PRECISE


class CmcStandardRate(models.Model):
    _name = "cmc.standard.rate"
    _inherit = ["cmc.standard.line.mixin"]
    _description = "Predetermined Rate"
    _order = "version_id, center_id, rate_kind"

    center_id = fields.Many2one(
        "account.analytic.account", required=True, check_company=True, ondelete="restrict",
        domain="[('cmc_center_type', '!=', False)]",
    )
    driver_id = fields.Many2one("cmc.cost.driver", check_company=True, ondelete="restrict")
    rate_kind = fields.Selection([("labour", "Labour"), ("overhead", "Overhead")], required=True, default="overhead")
    capacity_basis = fields.Selection(
        [("practical", "Practical capacity"), ("normal", "Normal capacity"), ("manual", "Manual")],
        required=True, default="practical",
    )
    budget_fixed = fields.Float(digits=PRECISE)
    budget_variable = fields.Float(digits=PRECISE)
    capacity_qty = fields.Float(digits=PRECISE)
    budget_qty = fields.Float(digits=PRECISE)
    fixed_rate = fields.Float(digits=PRECISE, compute="_compute_rates", store=True, readonly=False)
    variable_rate = fields.Float(digits=PRECISE, compute="_compute_rates", store=True, readonly=False)
    rate = fields.Float(digits=PRECISE, compute="_compute_total_rate", store=True)

    _version_center_kind_uniq = models.UniqueIndex(
        "(version_id, center_id, rate_kind)", "One predetermined rate per center and kind in a version."
    )

    @api.depends("capacity_basis", "budget_fixed", "budget_variable", "capacity_qty", "budget_qty")
    def _compute_rates(self):
        for line in self:
            if line.capacity_basis == "manual":
                continue
            line.fixed_rate = line.budget_fixed / line.capacity_qty if line.capacity_qty else 0.0
            level = line.budget_qty or line.capacity_qty
            line.variable_rate = line.budget_variable / level if level else 0.0

    @api.depends("fixed_rate", "variable_rate")
    def _compute_total_rate(self):
        for line in self:
            line.rate = line.fixed_rate + line.variable_rate
