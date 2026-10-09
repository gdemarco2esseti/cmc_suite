from odoo import api, models
from odoo.tools import float_is_zero

MOD_XMLID = "cmc_base.cmc_element_mod"


class AccountAnalyticLine(models.Model):
    _inherit = "account.analytic.line"

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        lines._cmc_stamp_project_object()
        return lines

    def write(self, vals):
        # why here too: hr_timesheet refuses foreign lines first, hiding the CMC allocation lock message
        self._cmc_check_relocation_lock([vals])
        # must sit above hr_timesheet in the MRO: its checks refuse foreign writes
        if self._cmc_classification_gate(vals):
            return True
        res = super().write(vals)
        # why task_id: project_id is a stored compute on task_id, a task-only write moves the line
        if "project_id" in vals or "task_id" in vals:
            self._cmc_stamp_project_object()
        return res

    def _cmc_stamp_project_object(self):
        """Hours on a project are cost of that project (AD-106); one batch per company."""
        # why sudo: employees logging hours have no CMC group nor rights on the registry
        lines = self.sudo().filtered("project_id")
        for company in lines.company_id:
            company_lines = lines.filtered(lambda line: line.company_id == company)
            objects = self.env["cmc.cost.object"]._cmc_ensure_objects(
                company_lines.project_id, company=company, object_type="project"
            )
            by_project = dict(zip(company_lines.project_id.ids, objects.ids, strict=True))
            for project, group in company_lines.grouped("project_id").items():
                group.write({"cmc_cost_object_id": by_project[project.id]})

    @api.model
    def _cmc_classify_trigger_fields(self):
        # the timesheet grid, the portal and most imports create the row first
        # and write project/employee/hours afterwards.
        return super()._cmc_classify_trigger_fields() | {
            "project_id", "employee_id", "unit_amount",
        }

    def _cmc_is_timesheet_mod(self):
        """A timesheet line booking DIRECT labor.

        Having a project, an employee and hours is not enough: direct labor is
        by definition attributable to the cost object, so only hours landing on
        a FINAL (productive) cost center are MOD. Hours booked on auxiliary,
        administrative, R&D or period centers are indirect labor and must stay
        indirect, otherwise the direct/indirect split the whole suite rests on
        is wrong at the source.
        """
        self.ensure_one()
        if not (self.project_id and self.employee_id):
            return False
        if float_is_zero(self.unit_amount, precision_digits=2):
            return False
        return self.cmc_center_id.cmc_center_type == "final"

    @api.model
    def _cmc_bridge_profiles(self):
        # direct labor (MOD): hours on a final center, scaling with the hours
        return super()._cmc_bridge_profiles() + [{
            "key": "timesheet_mod",
            "match": "_cmc_is_timesheet_mod",
            "element": MOD_XMLID,
            "behavior": "variable",
            "directness": "direct",
        }]
