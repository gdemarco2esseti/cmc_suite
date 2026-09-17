from odoo import api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import float_is_zero

from .cmc_utils import largest_remainder


class CmcAllocation(models.Model):
    _name = "cmc.allocation"
    _description = "Cost Allocation"
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
            rounding = (alloc.company_id or self.env.company).currency_id.rounding
            base = sum(alloc.line_ids.mapped("quantity"))
            if (
                alloc.amount
                and alloc.line_ids
                and float_is_zero(base, precision_rounding=rounding)
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
        if float_is_zero(total_weight, precision_rounding=rounding):
            return dict.fromkeys(weights, 0.0)
        raw = {k: self.amount * w / total_weight for k, w in weights.items()}
        return largest_remainder(self.amount, raw, rounding)


class CmcAllocationLine(models.Model):
    _name = "cmc.allocation.line"
    _description = "Cost Allocation Line"
    _check_company_auto = True

    allocation_id = fields.Many2one(
        "cmc.allocation", required=True, ondelete="cascade", index=True
    )
    company_id = fields.Many2one(
        related="allocation_id.company_id", store=True, index=True
    )
    currency_id = fields.Many2one(related="allocation_id.currency_id")
    target_account_id = fields.Many2one(
        "account.analytic.account", required=True, check_company=True
    )
    quantity = fields.Float(string="Driver Quantity", required=True)
    allocated_amount = fields.Monetary(
        compute="_compute_allocated_amount", store=True
    )

    _quantity_non_negative = models.Constraint(
        "CHECK(quantity >= 0)",
        "A driver quantity cannot be negative: it would invert the allocation.",
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
