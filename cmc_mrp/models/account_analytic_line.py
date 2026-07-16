from odoo import models


class AccountAnalyticLine(models.Model):
    _inherit = "account.analytic.line"

    def _cmc_auto_classify(self):
        """Enrich native manufacturing-order labor lines (created by mrp_account
        with category 'manufacturing_order') as direct labor (MOD). Fill-only
        and manual-safe; delegates the rest to the financial-account engine."""
        mod = self.env.ref(
            "cmc_base.cmc_element_mod", raise_if_not_found=False
        )
        for line in self:
            if line.cmc_classified_manually or line.category != "manufacturing_order":
                continue
            vals = {}
            if mod and not line.cmc_cost_element_id:
                vals["cmc_cost_element_id"] = mod.id
            if not line.cmc_behavior:
                vals["cmc_behavior"] = "variable"
            if line.cmc_directness != "direct":
                vals["cmc_directness"] = "direct"
            if vals:
                line.with_context(cmc_auto_classify=True).write(vals)
        return super()._cmc_auto_classify()
