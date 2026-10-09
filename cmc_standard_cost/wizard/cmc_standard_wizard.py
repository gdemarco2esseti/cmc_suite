from odoo import fields, models


class CmcStandardPrefillWizard(models.TransientModel):
    _name = "cmc.standard.prefill.wizard"
    _description = "Prefill Predetermined Rates"

    version_id = fields.Many2one("cmc.plan.version", required=True, readonly=True)
    budget_version_id = fields.Many2one(
        "cmc.plan.version", string="Budget Version", required=True, domain="[('version_type', 'in', ('budget', 'forecast')), ('state', '=', 'done')]",
    )
    basis = fields.Selection([("practical", "Practical capacity"), ("normal", "Normal capacity")], required=True, default="practical")

    def action_prefill(self):
        self.ensure_one()
        self.version_id.action_cmc_std_prefill_rates(self.budget_version_id, basis=self.basis)
        return {"type": "ir.actions.act_window_close"}


class CmcStandardRollupWizard(models.TransientModel):
    _name = "cmc.standard.rollup.wizard"
    _description = "Roll Up Standard Cost Cards"

    version_id = fields.Many2one("cmc.plan.version", required=True, readonly=True)
    product_ids = fields.Many2many("product.product", string="Products", required=True, domain="[('bom_ids', '!=', False)]")

    def action_rollup(self):
        self.ensure_one()
        self.version_id.action_cmc_std_rollup(self.product_ids)
        return {"type": "ir.actions.act_window_close"}
