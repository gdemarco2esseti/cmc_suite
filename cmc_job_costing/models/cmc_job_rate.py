from odoo import api, fields, models
from odoo.exceptions import ValidationError

from odoo.addons.cmc_base.models.cmc_utils import month_end


class CmcJobRate(models.Model):
    _name = "cmc.job.rate"
    _inherit = ["cmc.month.mixin"]
    _description = "Resource Center Hourly Rate"
    _order = "date desc, center_id, id"
    _check_company_auto = True

    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    center_id = fields.Many2one("account.analytic.account", string="Resource Center", required=True, check_company=True, index=True)
    category_id = fields.Many2one(
        "hr.employee.category", string="Employee Category", index=True,
        help="Empty = every employee of the center without a rate of their own category.",
    )
    rate_mode = fields.Selection([("manual", "Manual"), ("computed", "Computed")], default="manual", required=True)
    hourly_rate = fields.Float(compute="_compute_hourly_rate", store=True, readonly=False, digits=(16, 4))
    special_cost = fields.Monetary(help="Direct cost of the center in the month.")
    common_cost_share = fields.Monetary(help="Share of the common costs charged to the center.")
    gross_hours = fields.Float()
    absence_hours = fields.Float(help="Vacation, holidays, sick leave.")
    net_hours = fields.Float(compute="_compute_net_hours", store=True)

    _center_category_month_uniq = models.UniqueIndex(
        "(company_id, center_id, COALESCE(category_id, 0), date)",
        "A resource center has one rate per category and month.",
    )

    @api.depends("gross_hours", "absence_hours")
    def _compute_net_hours(self):
        for rate in self:
            rate.net_hours = rate.gross_hours - rate.absence_hours

    @api.depends("rate_mode", "special_cost", "common_cost_share", "net_hours")
    def _compute_hourly_rate(self):
        for rate in self:
            if rate.rate_mode == "computed":
                rate.hourly_rate = (rate.special_cost + rate.common_cost_share) / rate.net_hours if rate.net_hours > 0 else 0.0
            else:
                # why: a manual rate is an input, the compute must hand back what is stored
                rate.hourly_rate = rate._origin.hourly_rate if rate._origin else rate.hourly_rate

    @api.constrains("rate_mode", "net_hours", "hourly_rate")
    def _check_rate(self):
        for rate in self:
            if rate.hourly_rate < 0:
                raise ValidationError(self.env._("The hourly rate cannot be negative."))
            if rate.rate_mode == "computed" and rate.net_hours <= 0:
                raise ValidationError(self.env._("A computed rate needs net hours above zero."))

    def action_cmc_load_center_cost(self):
        for (company, month), rates in self.grouped(lambda rate: (rate.company_id, rate.date)).items():
            costs = rates.center_id._cmc_cost_groups(month, month_end(month), company=company)
            for rate in rates:
                rate.special_cost = costs.get(rate.center_id.id, 0.0)
        return True
