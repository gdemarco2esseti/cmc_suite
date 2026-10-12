from odoo import fields, models

from .cmc_kpi import PERSPECTIVES


class CmcKpiScorecard(models.Model):
    _name = "cmc.kpi.scorecard"
    _description = "Balanced Scorecard"
    _order = "name, id"
    _check_company_auto = True

    name = fields.Char(required=True, translate=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    active = fields.Boolean(default=True)
    responsibility_id = fields.Many2one("cmc.responsibility.center", string="Responsibility Center", check_company=True)
    perspective_ids = fields.One2many("cmc.kpi.scorecard.perspective", "scorecard_id", string="Perspectives", copy=True)
    line_ids = fields.One2many("cmc.kpi.scorecard.line", "scorecard_id", string="KPIs", copy=True)


class CmcKpiScorecardPerspective(models.Model):
    _name = "cmc.kpi.scorecard.perspective"
    _description = "Balanced Scorecard Perspective"
    _order = "scorecard_id, id"

    scorecard_id = fields.Many2one("cmc.kpi.scorecard", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="scorecard_id.company_id", store=True, index=True)
    perspective = fields.Selection(PERSPECTIVES, required=True)
    weight = fields.Float(help="Percent; the perspectives of a scorecard total 100.")

    _scorecard_perspective_uniq = models.UniqueIndex("(scorecard_id, perspective)", "A perspective is weighted once per scorecard.")


class CmcKpiScorecardLine(models.Model):
    _name = "cmc.kpi.scorecard.line"
    _description = "Balanced Scorecard KPI"
    _order = "scorecard_id, perspective, id"
    _check_company_auto = True

    scorecard_id = fields.Many2one("cmc.kpi.scorecard", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="scorecard_id.company_id", store=True, index=True)
    kpi_id = fields.Many2one("cmc.kpi", string="KPI", required=True, ondelete="cascade", check_company=True)
    perspective = fields.Selection(related="kpi_id.perspective", store=True)
    weight = fields.Float(help="Percent; the KPIs of a perspective total 100.")
