from odoo import api, models
from odoo.tools import float_is_zero

MOD_XMLID = "cmc_base.cmc_element_mod"


class AccountAnalyticLine(models.Model):
    _inherit = "account.analytic.line"

    def write(self, vals):
        # must sit above hr_timesheet in the MRO: its checks refuse foreign writes
        if self._cmc_classification_gate(vals):
            return True
        return super().write(vals)

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
