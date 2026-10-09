from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

from odoo.addons.cmc_base.models.cmc_utils import month_start


class CmcEvmChangeRequest(models.Model):
    _name = "cmc.evm.change.request"
    _description = "Baseline Change Request"
    _order = "date desc, id desc"
    _check_company_auto = True

    name = fields.Char(required=True)
    baseline_id = fields.Many2one(
        "cmc.plan.version", required=True, ondelete="restrict", index=True, domain="[('version_type', '=', 'baseline')]",
    )
    company_id = fields.Many2one(related="baseline_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="baseline_id.currency_id")
    task_id = fields.Many2one("project.task", required=True, check_company=True, ondelete="restrict")
    date = fields.Date(required=True, default=fields.Date.context_today)
    cost_impact = fields.Monetary()
    state = fields.Selection([("draft", "Draft"), ("approved", "Approved"), ("rejected", "Rejected")], default="draft", required=True, copy=False)
    new_baseline_id = fields.Many2one("cmc.plan.version", readonly=True, copy=False, ondelete="set null")

    @api.constrains("baseline_id", "task_id", "date")
    def _check_scope(self):
        for cr in self:
            baseline = cr.baseline_id
            if baseline.version_type != "baseline":
                raise ValidationError(self.env._("A change request amends a baseline."))
            if cr.task_id.project_id != baseline.project_id:
                raise ValidationError(self.env._("The change must fall on a task of the baseline job."))
            if not baseline.date_from <= cr.date <= baseline.date_to:
                raise ValidationError(self.env._("The change must fall inside the baseline span."))

    def action_approve(self):
        self.env["cmc.plan.version"]._cmc_check_manager()
        for cr in self:
            if cr.state != "draft":
                raise UserError(self.env._("%s is not a draft change request.", cr.name))
            if cr.baseline_id.state != "done":
                raise UserError(self.env._("%s: approve the baseline before amending it.", cr.name))
            new = cr._newest_in_lineage().copy()
            first = month_start(cr.date)
            self.env["cmc.evm.estimate.line"].create({
                "version_id": new.id, "name": cr.name, "task_id": cr.task_id.id, "material_cost": cr.cost_impact,
                "date_start": first, "date_end": first,
            })
            new._cmc_evm_plan()
            cr.write({"new_baseline_id": new.id, "state": "approved"})
        return True

    def _newest_in_lineage(self):
        self.ensure_one()
        version = self.baseline_id
        while child := self.env["cmc.plan.version"].search([("parent_id", "=", version.id)], order="id desc", limit=1):
            version = child
        return version

    def action_reject(self):
        if any(cr.state != "draft" for cr in self):
            raise UserError(self.env._("Only a draft change request can be rejected."))
        self.write({"state": "rejected"})
        return True
