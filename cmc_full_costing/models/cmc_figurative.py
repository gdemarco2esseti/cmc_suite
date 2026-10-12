from odoo import api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import float_is_zero

# `rate` carries 6 decimals (see its digits)
RATE_ROUNDING = 1e-6


class CmcFigurativeCost(models.Model):
    _name = "cmc.figurative.cost"
    _description = "Figurative cost (cost accounting only, economic-technical)"
    _order = "date_to desc, id desc"
    _check_company_auto = True

    name = fields.Char(required=True)
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company, index=True
    )
    currency_id = fields.Many2one(related="company_id.currency_id")
    date_from = fields.Date()
    date_to = fields.Date()
    cost_element_id = fields.Many2one(
        "cmc.cost.element",
        required=True,
        index=True,
        check_company=True,
        domain="[('cmc_kind', '=', 'figurative')]",
    )
    method = fields.Selection(
        selection=[("lump", "Lump sum"), ("interest", "Interest on capital")],
        required=True,
        default="lump",
    )
    capital = fields.Monetary()
    rate = fields.Float(string="Annual Rate", digits=(16, 6))
    # plain stored value: a compute that skips branches is nulled by the ORM
    # for the records it leaves unset, which silently zeroed lump-sum amounts
    # while editing the form. The interest method derives it instead (below).
    amount = fields.Monetary()
    cost_object_ref = fields.Reference(
        selection="_selection_cost_object", string="Cost Object"
    )
    cmc_directness = fields.Selection(
        selection=[("direct", "Direct"), ("indirect", "Indirect")],
        default="indirect",
    )

    @api.model
    def _selection_cost_object(self):
        return self.env["account.analytic.line"]._selection_cost_object()

    def _has_interest_inputs(self):
        """True once capital or rate is filled in. Without them the interest
        formula yields 0 and would wipe an amount the user typed by hand."""
        self.ensure_one()
        rounding = self.currency_id.rounding or 0.01
        return not (
            float_is_zero(self.capital, precision_rounding=rounding)
            and float_is_zero(self.rate, precision_rounding=RATE_ROUNDING)
        )

    @api.onchange("method", "capital", "rate")
    def _onchange_interest_amount(self):
        for rec in self:
            if rec.method == "interest" and rec._has_interest_inputs():
                rec.amount = rec.rate * rec.capital

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("method") == "interest" and "amount" not in vals:
                vals["amount"] = vals.get("rate", 0.0) * vals.get("capital", 0.0)
        return super().create(vals_list)

    def write(self, vals):
        res = super().write(vals)
        # the form keeps `amount` read-only under the interest method, so the
        # client never sends it: re-derive it server-side (RPC included)
        if "amount" not in vals and {"method", "capital", "rate"} & vals.keys():
            for rec in self:
                if rec.method == "interest" and rec._has_interest_inputs():
                    rec.write({"amount": rec.rate * rec.capital})
        return res

    @api.constrains("cost_element_id")
    def _check_figurative_element(self):
        for rec in self:
            if rec.cost_element_id.cmc_kind != "figurative":
                raise ValidationError(
                    self.env._(
                        "Figurative cost %(name)s must use a figurative cost "
                        "element.",
                        name=rec.name,
                    )
                )
