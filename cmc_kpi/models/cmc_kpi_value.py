from odoo import fields, models

from .cmc_kpi import PERSPECTIVES


class CmcKpiValue(models.Model):
    _name = "cmc.kpi.value"
    _inherit = ["cmc.run.result.mixin"]
    _description = "KPI Value"
    _order = "date, kpi_id"
    _rec_name = "code"

    run_id = fields.Many2one("cmc.kpi.run", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="run_id.company_id", store=True, index=True)
    kpi_id = fields.Many2one("cmc.kpi", string="KPI", required=True, ondelete="restrict", index=True)
    code = fields.Char(related="kpi_id.code", store=True)
    name = fields.Char(related="kpi_id.name")
    perspective = fields.Selection(related="kpi_id.perspective", store=True)
    responsibility_id = fields.Many2one(related="kpi_id.responsibility_id", store=True, index=True)
    date = fields.Date(string="Month", required=True, index=True)
    value = fields.Float(aggregator="avg", digits=(16, 4))
    computable = fields.Boolean()
    reason = fields.Char(help="Why the value is not computable; empty when it is.")
    target = fields.Float(aggregator="avg", digits=(16, 4))
    target_source = fields.Selection([("version", "Plan version"), ("static", "KPI")])
    gap_pct = fields.Float(string="Gap %", aggregator="avg")
    status = fields.Selection(
        [("green", "On target"), ("amber", "Attention"), ("red", "Off target"), ("none", "Not computable")], default="none"
    )
    score = fields.Float(aggregator="avg")
    previous_value = fields.Float(digits=(16, 4), aggregator=False)
    trend_delta = fields.Float(digits=(16, 4), aggregator=False)
    trend = fields.Selection(
        [("improving", "Improving"), ("worsening", "Worsening"), ("stable", "Stable"), ("none", "No trend")], default="none"
    )


class CmcKpiScorecardResult(models.Model):
    _name = "cmc.kpi.scorecard.result"
    _inherit = ["cmc.run.result.mixin"]
    _description = "Balanced Scorecard Result"
    _order = "date, scorecard_id, id"

    run_id = fields.Many2one("cmc.kpi.run", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="run_id.company_id", store=True, index=True)
    scorecard_id = fields.Many2one("cmc.kpi.scorecard", required=True, ondelete="restrict", index=True)
    date = fields.Date(string="Month", required=True, index=True)
    perspective = fields.Selection(PERSPECTIVES, help="Empty = the overall score of the scorecard.")
    weight = fields.Float(aggregator=False)
    score = fields.Float(aggregator="avg")
    computable = fields.Boolean()
