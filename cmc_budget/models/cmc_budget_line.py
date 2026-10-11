from odoo import api, fields, models
from odoo.exceptions import ValidationError

from odoo.addons.cmc_base.models.cmc_utils import month_start


class CmcBudgetLine(models.Model):
    _name = "cmc.budget.line"
    _inherit = ["cmc.month.mixin", "cmc.run.line.mixin"]
    _description = "Budget Line"
    _order = "date, center_id, id"
    _check_company_auto = True
    _cmc_run_field = "version_id"

    date = fields.Date(string="Month")
    version_id = fields.Many2one("cmc.plan.version", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="version_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="version_id.currency_id")
    company_currency_id = fields.Many2one(related="version_id.company_id.currency_id", string="Company Currency")
    kind = fields.Selection([("cost", "Cost"), ("revenue", "Revenue")], required=True, default="cost")
    center_id = fields.Many2one("account.analytic.account", string="Cost Center", check_company=True, index="btree_not_null")
    element_id = fields.Many2one("cmc.cost.element", string="Cost Element", check_company=True)
    behavior = fields.Selection([("fixed", "Fixed"), ("variable", "Variable")], required=True, default="fixed")
    driver_id = fields.Many2one("cmc.cost.driver", string="Driver", check_company=True)
    responsibility_id = fields.Many2one("cmc.responsibility.center", string="Area", check_company=True)
    business_line_id = fields.Many2one("account.analytic.account", string="Business Line", check_company=True)
    payment_term_id = fields.Many2one("account.payment.term", string="Payment Terms")
    figure = fields.Monetary(help="The amount as typed, always positive.")
    amount = fields.Monetary(
        compute="_compute_amount", inverse="_inverse_amount", store=True, help="Natural sign: cost negative, revenue positive."
    )
    amount_company = fields.Monetary(
        compute="_compute_amount_company", store=True, currency_field="company_currency_id",
        help="The amount converted at the fixed rate of the version.",
    )

    _version_date_idx = models.Index("(version_id, date)")

    @api.depends("figure", "kind")
    def _compute_amount(self):
        for line in self:
            line.amount = line.figure if line.kind == "revenue" else -line.figure

    def _inverse_amount(self):
        for line in self:
            line.figure = abs(line.amount)

    @api.depends("amount", "version_id.currency_rate", "version_id.company_id")
    def _compute_amount_company(self):
        for line in self:
            line.amount_company = line.version_id._cmc_to_company_currency(line.amount) if line.version_id else 0.0

    @api.constrains("kind", "center_id")
    def _check_cost_center(self):
        if self.filtered(lambda line: line.kind == "cost" and not line.center_id):
            raise ValidationError(self.env._("A cost budget line needs a cost center."))

    @api.constrains("date", "version_id")
    def _check_date_in_version(self):
        for line in self:
            version = line.version_id
            if not month_start(version.date_from) <= line.date <= version.date_to:
                raise ValidationError(self.env._("A budget line must fall inside the span of its version."))
