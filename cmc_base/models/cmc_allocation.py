from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.fields import Domain
from odoo.tools import float_is_zero

from .cmc_utils import largest_remainder


class CmcAllocation(models.Model):
    _name = "cmc.allocation"
    _inherit = ["cmc.run.mixin"]
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
    date = fields.Date(required=True, default=fields.Date.context_today, help="Posting date of the result.")
    element_id = fields.Many2one("cmc.cost.element", string="Cost Element", check_company=True)
    line_ids = fields.One2many("cmc.allocation.line", "allocation_id")
    posted_line_ids = fields.One2many("account.analytic.line", "cmc_allocation_id", string="Posted Lines")

    _cmc_input_fields = ("company_id", "source_account_id", "driver_id", "amount", "date", "element_id", "line_ids")
    _cmc_result_fields = ("posted_line_ids",)

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


    def _cmc_compute(self):
        self.ensure_one()
        shares = self._allocate_conserving()
        postings = [(line.target_account_id, -shares[line.id]) for line in self.line_ids]
        postings.append((self.source_account_id, self.amount))
        # fail loud: the relocation must not create or lose cost
        if self.currency_id.compare_amounts(sum(amount for _center, amount in postings), 0.0):
            raise ValidationError(self.env._("Allocation %(name)s does not conserve the amount.", name=self.name))
        self.env["account.analytic.line"].create([
            {
                "name": self.name,
                "date": self.date,
                "company_id": self.company_id.id,
                "amount": amount,
                center.plan_id._column_name(): center.id,
                "cmc_cost_element_id": self.element_id.id,
                "cmc_allocation_id": self.id,
            }
            for center, amount in postings
        ])

    def unlink(self):
        if self.filtered(lambda alloc: alloc.state == "done"):
            raise UserError(self.env._("A computed allocation cannot be deleted: reset it to draft first."))
        return super().unlink()

    @api.model
    def _cmc_allocated_share(self, company, date_from, date_to):
        """Share of the indirect cost of the span that is spread by convention (guardrail 10%)."""
        self.check_access("read")
        # why sudo: aggregates of the whole company, whatever the lines' rules show the caller
        Line = self.env["account.analytic.line"].sudo()
        overhead = Line._cmc_cost_read_group(
            Domain("company_id", "=", company.id) & Domain("cmc_excluded", "=", False)
            & Domain("cmc_directness", "=", "indirect") & Domain("cmc_allocation_id", "=", False)
            & Domain("date", ">=", date_from) & Domain("date", "<=", date_to)
        )[0][0]
        allocated = sum(
            amount
            for _driver, amount in self.sudo()._read_group(
                Domain("company_id", "=", company.id) & Domain("state", "=", "done")
                & Domain("driver_id.assignment", "=", "allocated")
                & Domain("date", ">=", date_from) & Domain("date", "<=", date_to),
                ["driver_id"],
                ["amount:sum"],
            )
        )
        share = allocated / overhead * 100 if overhead else 0.0
        return {"total_overhead": overhead, "allocated": allocated, "share": share, "warning": share > 10}


class CmcAllocationLine(models.Model):
    _name = "cmc.allocation.line"
    _inherit = ["cmc.run.line.mixin"]
    _description = "Cost Allocation Line"
    _check_company_auto = True
    _cmc_run_field = "allocation_id"

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
