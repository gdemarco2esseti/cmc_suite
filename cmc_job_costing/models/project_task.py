from odoo import api, fields, models
from odoo.exceptions import ValidationError


class ProjectTask(models.Model):
    _inherit = "project.task"

    cmc_progress = fields.Float(string="Physical Progress (%)", copy=False, help="Earned value reads this field, not the timesheet progress.")

    @api.constrains("cmc_progress")
    def _check_cmc_progress(self):
        if any(not 0 <= task.cmc_progress <= 100 for task in self):
            raise ValidationError(self.env._("The progress is a percent between 0 and 100."))
