from odoo import api, fields, models
from odoo.exceptions import ValidationError

from odoo.addons.cmc_base.models import cmc_utils


class CmcEvmEstimateLine(models.Model):
    _name = "cmc.evm.estimate.line"
    _inherit = ["cmc.run.line.mixin"]
    _description = "Baseline Estimate Line"
    _order = "version_id, date_start, id"
    _check_company_auto = True

    _cmc_run_field = "version_id"

    version_id = fields.Many2one("cmc.plan.version", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="version_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="version_id.currency_id")
    name = fields.Char(required=True)
    task_id = fields.Many2one("project.task", string="Work Package", required=True, check_company=True, ondelete="restrict", index=True)
    center_id = fields.Many2one("account.analytic.account", string="Resource Center", check_company=True)
    hours_optimistic = fields.Float()
    hours_most_likely = fields.Float()
    hours_pessimistic = fields.Float()
    material_cost = fields.Monetary()
    date_start = fields.Date(required=True)
    date_end = fields.Date(required=True)
    hourly_rate = fields.Float(digits=(16, 4), readonly=True, copy=True, help="Set from the rate card when the baseline is planned.")
    hours_expected = fields.Float(compute="_compute_hours_expected", store=True)
    cost_optimistic = fields.Monetary(compute="_compute_costs", store=True)
    cost_most_likely = fields.Monetary(compute="_compute_costs", store=True)
    cost_pessimistic = fields.Monetary(compute="_compute_costs", store=True)
    cost_expected = fields.Monetary(compute="_compute_costs", store=True)
    cost_sigma = fields.Float(compute="_compute_costs", store=True)

    @api.depends("hours_optimistic", "hours_most_likely", "hours_pessimistic")
    def _compute_hours_expected(self):
        for line in self:
            try:
                line.hours_expected = cmc_utils.pert_estimate(line.hours_optimistic, line.hours_most_likely, line.hours_pessimistic)[0]
            except ValueError:  # why: refused by _check_hours; the compute must not mask that message
                line.hours_expected = 0.0

    @api.depends("hours_optimistic", "hours_most_likely", "hours_pessimistic", "hourly_rate", "material_cost")
    def _compute_costs(self):
        for line in self:
            points = [h * line.hourly_rate + line.material_cost for h in (line.hours_optimistic, line.hours_most_likely, line.hours_pessimistic)]
            try:
                expected, sigma = cmc_utils.pert_estimate(*points)
            except ValueError:  # why: refused by _check_hours
                points, expected, sigma = [0.0] * 3, 0.0, 0.0
            line.cost_optimistic, line.cost_most_likely, line.cost_pessimistic = points
            line.cost_expected, line.cost_sigma = expected, sigma

    @api.constrains("hours_optimistic", "hours_most_likely", "hours_pessimistic", "center_id")
    def _check_hours(self):
        for line in self:
            if not 0 <= line.hours_optimistic <= line.hours_most_likely <= line.hours_pessimistic:
                raise ValidationError(self.env._("%s: the hours need 0 <= optimistic <= most likely <= pessimistic.", line.name))
            if line.hours_pessimistic and not line.center_id:
                raise ValidationError(self.env._("%s: hours are valued at a resource center rate.", line.name))

    @api.constrains("task_id", "version_id")
    def _check_task_project(self):
        if any(line.version_id.project_id and line.task_id.project_id != line.version_id.project_id for line in self):
            raise ValidationError(self.env._("The work package must be a task of the baseline job."))

    @api.constrains("date_start", "date_end", "version_id")
    def _check_schedule(self):
        for line in self:
            if line.date_end < line.date_start:
                raise ValidationError(self.env._("%s: the end precedes the start.", line.name))
            if line.date_start < line.version_id.date_from or line.date_end > line.version_id.date_to:
                raise ValidationError(self.env._("%s: schedule it inside the baseline span.", line.name))


class CmcEvmBaselineLine(models.Model):
    _name = "cmc.evm.baseline.line"
    _description = "Baseline Planned Value"
    _order = "version_id, task_id, date"
    _check_company_auto = True

    version_id = fields.Many2one("cmc.plan.version", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="version_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="version_id.currency_id")
    task_id = fields.Many2one("project.task", required=True, ondelete="restrict", index=True)
    date = fields.Date(required=True, help="First day of the month.")
    planned_value = fields.Monetary()


class CmcEvmRisk(models.Model):
    _name = "cmc.evm.risk"
    _inherit = ["cmc.run.line.mixin"]
    _description = "Baseline Risk"
    _order = "emv desc, id"
    _check_company_auto = True

    _cmc_run_field = "version_id"

    version_id = fields.Many2one("cmc.plan.version", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="version_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="version_id.currency_id")
    name = fields.Char(required=True)
    task_id = fields.Many2one("project.task", check_company=True, ondelete="set null")
    probability = fields.Float(help="Percent, 0 to 100.")
    impact = fields.Monetary(help="Negative: an opportunity.")
    emv = fields.Monetary(string="EMV", compute="_compute_emv", store=True)

    @api.depends("probability", "impact")
    def _compute_emv(self):
        for risk in self:
            try:
                risk.emv = cmc_utils.emv(risk.probability / 100.0, risk.impact)
            except ValueError:  # why: refused by _check_probability
                risk.emv = 0.0

    @api.constrains("probability")
    def _check_probability(self):
        if any(not 0 <= risk.probability <= 100 for risk in self):
            raise ValidationError(self.env._("A probability is a percent between 0 and 100."))
