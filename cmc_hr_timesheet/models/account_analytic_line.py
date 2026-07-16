from odoo import models


class AccountAnalyticLine(models.Model):
    _inherit = "account.analytic.line"

    def _cmc_is_timesheet_mod(self):
        """A timesheet line booking direct labor: has a project, an employee
        and logged hours. Timesheets are plain account.analytic.line records
        (hr_timesheet extends the model), so they flow through the same
        classification engine but carry no financial account to derive from."""
        self.ensure_one()
        return bool(self.project_id) and bool(self.employee_id) and bool(
            self.unit_amount
        )

    def _cmc_auto_classify(self):
        """Enrich timesheet labor lines as MOD (direct labor) before delegating
        to the financial-account engine. Fill-only and manual-safe."""
        mod = self.env.ref(
            "cmc_base.cmc_element_mod", raise_if_not_found=False
        )
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
                line.with_context(cmc_auto_classify=True).write(vals)
        return super()._cmc_auto_classify()
