from odoo import api, fields, models
from odoo.exceptions import ValidationError


class CmcFigurativeCost(models.Model):
    _name = "cmc.figurative.cost"
    _description = "Figurative cost (co.an.-only, economico-tecnico)"
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
    amount = fields.Monetary(compute="_compute_amount", store=True, readonly=False)
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

    @api.depends("method", "capital", "rate")
    def _compute_amount(self):
        for rec in self:
            if rec.method == "interest":
                rec.amount = rec.rate * rec.capital

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
