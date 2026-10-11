from odoo import api, fields, models

from odoo.addons.cmc_base.models.cmc_utils import format_figure

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
        [("green", "On target"), ("amber", "Attention"), ("red", "Off target"), ("none", "Not computable"), ("no_data", "No data")],
        default="none",
    )
    score = fields.Float(aggregator="avg")
    previous_value = fields.Float(digits=(16, 4), aggregator=False)
    trend_delta = fields.Float(digits=(16, 4), aggregator=False)
    trend = fields.Selection(
        [("improving", "Improving"), ("worsening", "Worsening"), ("stable", "Stable"), ("none", "No trend")], default="none"
    )
    value_display = fields.Char(string="KPI Value", compute="_compute_display_figures")
    target_display = fields.Char(string="KPI Target", compute="_compute_display_figures")

    @api.depends("value", "target", "kpi_id.unit", "kpi_id.source_type")
    def _compute_display_figures(self):
        for rec in self:
            # why: a percent financial ratio is a fraction (0.9076), the same figure the ratio list shows as 90.76 %
            percent, fraction = rec.kpi_id.unit == "percent", bool(rec.kpi_id) and rec.kpi_id._cmc_is_fraction_percent()
            rec.value_display = format_figure(self.env, rec.value, percent, fraction, digits=4)
            rec.target_display = format_figure(self.env, rec.target, percent, fraction, digits=4)


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
