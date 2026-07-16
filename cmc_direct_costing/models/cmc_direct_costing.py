from odoo import api, fields, models


class CmcDirectCosting(models.Model):
    _name = "cmc.direct.costing"
    _description = "Direct Costing / Contribution Margin Analysis"
    _order = "date_to desc, name"
    _check_company_auto = True

    name = fields.Char(required=True, default=lambda s: s.env._("Direct Costing"))
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda s: s.env.company, index=True
    )
    currency_id = fields.Many2one(related="company_id.currency_id")
    date_from = fields.Date(required=True, default=fields.Date.context_today)
    date_to = fields.Date(required=True, default=fields.Date.context_today)
    mode = fields.Selection(
        [("simple", "Simple (1 margin)"), ("evoluto", "Evolved (2 margins)")],
        default="evoluto", required=True,
    )
    common_fixed_cost = fields.Monetary(string="Common Fixed Costs")
    line_ids = fields.One2many("cmc.direct.costing.line", "analysis_id")

    total_mc_gross = fields.Monetary(compute="_compute_totals", store=True)
    total_mc_semigross = fields.Monetary(compute="_compute_totals", store=True)
    operating_result = fields.Monetary(compute="_compute_totals", store=True)

    _dates_ok = models.Constraint(
        "CHECK(date_to >= date_from)", "End date must be on/after start date."
    )

    @api.depends("line_ids.mc_gross", "line_ids.mc_semigross", "common_fixed_cost")
    def _compute_totals(self):
        for rec in self:
            rec.total_mc_gross = sum(rec.line_ids.mapped("mc_gross"))
            rec.total_mc_semigross = sum(rec.line_ids.mapped("mc_semigross"))
            rec.operating_result = rec.total_mc_semigross - rec.common_fixed_cost


class CmcDirectCostingLine(models.Model):
    _name = "cmc.direct.costing.line"
    _description = "Direct Costing Line (product/line margin)"

    analysis_id = fields.Many2one(
        "cmc.direct.costing", required=True, ondelete="cascade", index=True
    )
    company_id = fields.Many2one(related="analysis_id.company_id", store=True)
    currency_id = fields.Many2one(related="analysis_id.currency_id")
    name = fields.Char(string="Product / Line", required=True)
    revenue = fields.Monetary()
    variable_cost = fields.Monetary()
    specific_fixed_cost = fields.Monetary(string="Specific Fixed Costs")
    mc_gross = fields.Monetary(
        string="Gross MC", compute="_compute_margins", store=True
    )
    mc_semigross = fields.Monetary(
        string="Semigross MC", compute="_compute_margins", store=True
    )
    mc_ratio = fields.Float(
        string="MC %", compute="_compute_margins", store=True, digits=(16, 2)
    )

    @api.depends("revenue", "variable_cost", "specific_fixed_cost")
    def _compute_margins(self):
        for line in self:
            line.mc_gross = line.revenue - line.variable_cost
            line.mc_semigross = line.mc_gross - line.specific_fixed_cost
            line.mc_ratio = (
                line.mc_gross / line.revenue * 100 if line.revenue else 0.0
            )
