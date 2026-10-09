from odoo import api, models
from odoo.tools import float_is_zero

from odoo.addons.cmc_account.models.account_analytic_line import CMC_CLASSIFICATION_FIELDS

MOD_XMLID = "cmc_base.cmc_element_mod"


class AccountAnalyticLine(models.Model):
    _inherit = "account.analytic.line"

    def write(self, vals):
        if (
            not self.env.su
            and vals
            and set(vals) <= CMC_CLASSIFICATION_FIELDS
            and self.env.user.has_group("cmc_base.group_cmc_user")
        ):
            self.check_access("read")
            # why sudo: the queue sends a CMC controller to lines owned by other
            # users, and hr_timesheet (_check_can_write + record rules) refuses
            # any foreign write. Only CMC classification fields pass this gate,
            # and only on lines the user can read. sudo re-enters write(), so
            # the manual marking in cmc_account still runs. Never for an
            # excluded twin: sudo would defeat the superuser-only guard of
            # cmc_base, which must refuse the write as the user.
            if not any(self.mapped("cmc_excluded")):
                return self.sudo().write(vals)
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
            if line.cmc_classified_manually or not line._cmc_is_timesheet_mod():
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
