from odoo import fields, models


class CmcStdPushWizard(models.TransientModel):
    _name = "cmc.std.push.wizard"
    _description = "Push Standard Prices"

    version_id = fields.Many2one("cmc.plan.version", required=True, readonly=True)
    line_ids = fields.One2many("cmc.std.push.wizard.line", "wizard_id")

    def action_confirm(self):
        self.ensure_one()
        self.version_id.with_context(cmc_std_push_confirmed=True).action_cmc_std_push_prices()
        return {"type": "ir.actions.act_window_close"}


class CmcStdPushWizardLine(models.TransientModel):
    _name = "cmc.std.push.wizard.line"
    _description = "Push Standard Prices Line"

    wizard_id = fields.Many2one("cmc.std.push.wizard", required=True, ondelete="cascade")
    product_id = fields.Many2one("product.product", readonly=True)
    old_cost = fields.Float(string="Current Cost", digits="Product Price", readonly=True)
    new_cost = fields.Float(string="New Cost", digits="Product Price", readonly=True)
