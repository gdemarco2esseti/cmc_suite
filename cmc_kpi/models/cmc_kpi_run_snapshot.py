from odoo import fields, models

# the keys of cmc.plan.version._cmc_bva_summary kept in the snapshot
BVA_KEYS = ("plan_month", "actual_month", "variance_month", "variance_month_pct", "plan_ytd", "actual_ytd", "variance_ytd", "variance_ytd_pct", "alert_month", "alert_ytd")


class CmcKpiRunBva(models.Model):
    _name = "cmc.kpi.run.bva"
    _inherit = ["cmc.run.result.mixin"]
    _description = "KPI Run Budget vs Actual Snapshot"
    _order = "date, id"

    run_id = fields.Many2one("cmc.kpi.run", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="run_id.company_id", store=True, index=True)
    date = fields.Date(string="Month", required=True, index=True)
    center_id = fields.Many2one("account.analytic.account", string="Cost Center", ondelete="restrict")
    plan_month = fields.Float(aggregator="sum")
    actual_month = fields.Float(aggregator="sum")
    variance_month = fields.Float(aggregator="sum")
    variance_month_pct = fields.Float(aggregator="avg")
    plan_ytd = fields.Float(aggregator="sum")
    actual_ytd = fields.Float(aggregator="sum")
    variance_ytd = fields.Float(aggregator="sum")
    variance_ytd_pct = fields.Float(aggregator="avg")
    alert_month = fields.Boolean()
    alert_ytd = fields.Boolean()


class CmcKpiRunRatio(models.Model):
    _name = "cmc.kpi.run.ratio"
    _inherit = ["cmc.run.result.mixin"]
    _description = "KPI Run Key Ratio Snapshot"
    _order = "year, id"

    run_id = fields.Many2one("cmc.kpi.run", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="run_id.company_id", store=True, index=True)
    fin_analysis_id = fields.Many2one("cmc.fin.analysis", string="Financial Analysis", required=True, ondelete="restrict")
    year = fields.Integer(required=True, aggregator=False)
    code = fields.Char(required=True)
    name = fields.Char()
    value = fields.Float(aggregator="avg")
    status = fields.Selection([("ok", "OK"), ("alert", "Alert"), ("not_computable", "Not computable")])
