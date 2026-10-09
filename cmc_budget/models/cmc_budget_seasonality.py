from odoo import api, fields, models
from odoo.exceptions import ValidationError


class CmcBudgetSeasonality(models.Model):
    _name = "cmc.budget.seasonality"
    _description = "Budget Seasonality Profile"
    _order = "name"

    name = fields.Char(required=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    line_ids = fields.One2many("cmc.budget.seasonality.line", "seasonality_id", copy=True)

    def _cmc_weights(self):
        self.ensure_one()
        return {line.month: line.weight for line in self.line_ids}


class CmcBudgetSeasonalityLine(models.Model):
    _name = "cmc.budget.seasonality.line"
    _description = "Budget Seasonality Weight"
    _order = "seasonality_id, month"

    seasonality_id = fields.Many2one("cmc.budget.seasonality", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="seasonality_id.company_id", store=True, index=True)
    month = fields.Integer(required=True, help="Calendar month, 1 to 12.")
    weight = fields.Float(required=True, default=1.0)

    @api.constrains("month", "weight", "seasonality_id")
    def _check_month_weight(self):
        for line in self:
            if not 1 <= line.month <= 12:
                raise ValidationError(self.env._("The month of a seasonality weight must be between 1 and 12."))
            if line.weight < 0:
                raise ValidationError(self.env._("A seasonality weight cannot be negative."))
        for profile in self.seasonality_id:
            months = profile.line_ids.mapped("month")
            if len(months) != len(set(months)):
                raise ValidationError(self.env._("%s has two weights for the same month.", profile.display_name))
