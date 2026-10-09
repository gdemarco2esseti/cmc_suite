from odoo import api, fields, models
from odoo.exceptions import ValidationError

SPENDING_PROFILES = [
    ("flexible", "Flexible"),
    ("committed_fixed", "Committed - fixed"),
    ("committed_step", "Committed - step"),
]


class CmcAbcResource(models.Model):
    _inherit = "cmc.abc.resource"

    spending_profile = fields.Selection(
        SPENDING_PROFILES, required=True, default="committed_fixed",
        help="Flexible: bought as used. Committed fixed: the capacity on hand. Committed step: bought in lumps.",
    )
    unit_size = fields.Float(
        default=1.0, digits=(16, 6),
        help="Resource-driver units one supply unit provides (1,600 h per FTE, 800 shipments per truck). "
             "Committed profiles: per year.",
    )
    currency_id = fields.Many2one(related="company_id.currency_id")
    unit_cost = fields.Monetary(help="Cost of one supply unit. Committed profiles: per year.")
    committed_units = fields.Float(digits=(16, 6), help="Supply units on hand today (FTE on payroll, trucks owned).")
    budget_element_id = fields.Many2one(
        "cmc.cost.element", string="Budget Element", check_company=True,
        help="Cost element of the budget lines. Empty: the single cost element of the resource.",
    )
    idle_responsibility_id = fields.Many2one("cmc.responsibility.center", string="Idle Capacity Owner", check_company=True)
    nonproductive_responsibility_id = fields.Many2one(
        "cmc.responsibility.center", string="Non-productive Time Owner", check_company=True
    )

    @api.constrains("unit_size", "unit_cost", "committed_units")
    def _check_spending_profile(self):
        for resource in self:
            if resource.unit_size <= 0:
                raise ValidationError(self.env._("%s: the unit size must be greater than zero.", resource.display_name))
            if resource.unit_cost < 0 or resource.committed_units < 0:
                raise ValidationError(self.env._("%s: unit cost and committed units cannot be negative.", resource.display_name))
