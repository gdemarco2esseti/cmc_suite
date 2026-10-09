from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError

JOB_FIELDS = ("cmc_job_closed", "cmc_job_close_date")


class ProjectProject(models.Model):
    _inherit = "project.project"

    cmc_job_closed = fields.Boolean(string="Job Closed", copy=False, help="Transferred to finished goods.")
    cmc_job_close_date = fields.Date(string="Job Close Date", copy=False)

    @api.constrains("cmc_job_closed", "cmc_job_close_date")
    def _check_cmc_job_close_date(self):
        if any(project.cmc_job_closed and not project.cmc_job_close_date for project in self):
            raise ValidationError(self.env._("A closed job needs its close date."))

    def _cmc_check_job_close_rights(self, vals):
        if not self.env.su and not set(JOB_FIELDS).isdisjoint(vals) and not self.env.user.has_group("cmc_base.group_cmc_manager"):
            raise AccessError(self.env._("Only a Controlling Manager closes or reopens a job."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._cmc_check_job_close_rights(vals)
        return super().create(vals_list)

    def write(self, vals):
        self._cmc_check_job_close_rights(vals)
        return super().write(vals)

    def action_cmc_job_sheets(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Job Cost Sheets"),
            "res_model": "cmc.job.sheet",
            "view_mode": "list,form",
            "domain": [("project_id", "=", self.id)],
        }
