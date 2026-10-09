from odoo import api, fields, models
from odoo.exceptions import ValidationError

from odoo.addons.cmc_base.models.cmc_utils import month_start


class CmcKpiTarget(models.Model):
    _name = "cmc.kpi.target"
    _inherit = ["cmc.month.mixin", "cmc.run.line.mixin"]
    _description = "KPI Target"
    _order = "date, kpi_id"
    _check_company_auto = True
    _cmc_run_field = "version_id"

    version_id = fields.Many2one("cmc.plan.version", string="Plan Version", required=True, ondelete="cascade", index=True, check_company=True)
    company_id = fields.Many2one(related="version_id.company_id", store=True, index=True)
    kpi_id = fields.Many2one("cmc.kpi", string="KPI", required=True, ondelete="cascade", index=True, check_company=True)
    target_value = fields.Float()

    _version_kpi_month_uniq = models.UniqueIndex("(version_id, kpi_id, date)", "A KPI has one target per month and plan version.")

    @api.constrains("date", "version_id")
    def _check_date_in_version(self):
        for target in self:
            version = target.version_id
            if not month_start(version.date_from) <= target.date <= version.date_to:
                raise ValidationError(self.env._("A target must fall inside the span of its plan version."))


class CmcPlanVersion(models.Model):
    _inherit = "cmc.plan.version"

    kpi_target_ids = fields.One2many("cmc.kpi.target", "version_id", string="KPI Targets", copy=True)
