from odoo import api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import float_round


class CmcAllocation(models.Model):
    _name = "cmc.allocation"
    _description = "Cost Allocation (base di riparto)"
    _check_company_auto = True
    _check_company_domain = models.check_company_domain_parent_of

    name = fields.Char(required=True)
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company, index=True
    )
    currency_id = fields.Many2one(related="company_id.currency_id")
    source_account_id = fields.Many2one(
        "account.analytic.account",
        string="Common Cost Center",
        required=True,
        check_company=True,
        help="Center whose common cost is spread across the target centers.",
    )
    driver_id = fields.Many2one("cmc.cost.driver", required=True, check_company=True)
    amount = fields.Monetary(string="Amount to Allocate", required=True)
    line_ids = fields.One2many("cmc.allocation.line", "allocation_id")

    @api.constrains("amount", "line_ids")
    def _check_allocation_base(self):
        for alloc in self:
            if alloc.amount and alloc.line_ids and not sum(
                alloc.line_ids.mapped("quantity")
            ):
                raise ValidationError(
                    self.env._(
                        "Allocation %(name)s has an empty base (all driver "
                        "quantities are zero): the cost cannot be spread.",
                        name=alloc.name,
                    )
                )

    def _allocate_conserving(self):
        """Split amount across lines proportionally to driver quantity, using the
        largest-remainder method so the rounded parts re-sum to the exact total."""
        self.ensure_one()
        rounding = (self.company_id or self.env.company).currency_id.rounding
        weights = {line.id: line.quantity for line in self.line_ids}
        total_weight = sum(weights.values())
        if not total_weight:
            return {line_id: 0.0 for line_id in weights}
        raw = {k: self.amount * w / total_weight for k, w in weights.items()}
        rounded = {k: float_round(v, precision_rounding=rounding) for k, v in raw.items()}
        residual = float_round(
            self.amount - sum(rounded.values()), precision_rounding=rounding
        )
        if residual:
            step = rounding if residual > 0 else -rounding
            order = sorted(raw, key=lambda k: raw[k] - rounded[k], reverse=residual > 0)
            i = 0
            while float_round(residual, precision_rounding=rounding) != 0 and order:
                k = order[i % len(order)]
                rounded[k] = float_round(rounded[k] + step, precision_rounding=rounding)
                residual = float_round(residual - step, precision_rounding=rounding)
                i += 1
        return rounded


class CmcAllocationLine(models.Model):
    _name = "cmc.allocation.line"
    _description = "Cost Allocation Line"

    allocation_id = fields.Many2one(
        "cmc.allocation", required=True, ondelete="cascade", index=True
    )
    company_id = fields.Many2one(related="allocation_id.company_id", store=True)
    currency_id = fields.Many2one(related="allocation_id.currency_id")
    target_account_id = fields.Many2one(
        "account.analytic.account", required=True, check_company=True
    )
    quantity = fields.Float(string="Driver Quantity", required=True)
    allocated_amount = fields.Monetary(
        compute="_compute_allocated_amount", store=True
    )

    @api.depends(
        "quantity",
        "allocation_id.amount",
        "allocation_id.line_ids.quantity",
        "allocation_id.currency_id",
    )
    def _compute_allocated_amount(self):
        for alloc, lines in self.grouped("allocation_id").items():
            shares = alloc._allocate_conserving()
            for line in lines:
                line.allocated_amount = shares.get(line.id, 0.0)
