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

    def _cmc_auto_classify(self):
        """Enrich timesheet labor lines as MOD (direct labor) before delegating
        to the financial-account engine. Fill-only and manual-safe."""
        mod = self._cmc_element_ref(MOD_XMLID)
        vals_by_line = []
        for line in self:
            if (
                line.cmc_classified_manually
                or line.cmc_excluded
                or not line._cmc_is_timesheet_mod()
            ):
                continue
            vals = {}
            if mod and not line.cmc_cost_element_id:
                vals["cmc_cost_element_id"] = mod.id
            if not line.cmc_behavior:
                vals["cmc_behavior"] = "variable"  # labor scales with hours
            if line.cmc_directness != "direct":
                vals["cmc_directness"] = "direct"  # direct labor on the object
            if vals:
                vals_by_line.append((line.id, vals))
        self._cmc_write_batched(vals_by_line)
        return super()._cmc_auto_classify()
