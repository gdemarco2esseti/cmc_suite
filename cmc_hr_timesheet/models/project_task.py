from odoo import models


class ProjectTask(models.Model):
    _inherit = "project.task"

    def write(self, vals):
        res = super().write(vals)
        # why: moving a task recomputes its timesheets' project_id without a write() on them (AD-106)
        if "project_id" in vals:
            self.timesheet_ids._cmc_stamp_project_object()
        return res
