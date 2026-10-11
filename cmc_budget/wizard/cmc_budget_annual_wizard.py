from odoo import api, fields, models


class CmcBudgetAnnualWizard(models.TransientModel):
    _name = "cmc.budget.annual.wizard"
    _description = "Add Annual Budget"

    version_id = fields.Many2one(
        "cmc.plan.version", required=True, default=lambda self: self._default_version_id(),
        domain="[('state', '=', 'draft'), ('version_type', 'in', ('budget', 'forecast'))]",
    )
    company_id = fields.Many2one(related="version_id.company_id")
    currency_id = fields.Many2one(related="version_id.currency_id")
    kind = fields.Selection([("cost", "Cost"), ("revenue", "Revenue")], required=True, default="cost")
    center_id = fields.Many2one("account.analytic.account", string="Cost Center", check_company=True)
    element_id = fields.Many2one("cmc.cost.element", string="Cost Element", check_company=True)
    behavior = fields.Selection([("fixed", "Fixed"), ("variable", "Variable")], required=True, default="fixed")
    driver_id = fields.Many2one("cmc.cost.driver", string="Driver", check_company=True)
    amount = fields.Monetary(currency_field="currency_id", required=True, default=0.0, help="The annual figure, always positive.")
    seasonality_id = fields.Many2one(
        "cmc.budget.seasonality", check_company=True, compute="_compute_seasonality_id", store=True, readonly=False,
        help="Shapes variable costs and revenues over the months. A fixed cost is always spread evenly.",
    )

    def _default_version_id(self):
        if self.env.context.get("active_model") == "cmc.plan.version":
            return self.env.context.get("active_id")
        return False

    @api.depends("version_id")
    def _compute_seasonality_id(self):
        for wizard in self:
            wizard.seasonality_id = wizard.version_id.seasonality_id

    def action_apply(self):
        self.ensure_one()
        vals = {name: self[name].id if self._fields[name].type == "many2one" else self[name]
                for name in ("kind", "center_id", "element_id", "behavior", "driver_id") if self[name]}
        self.version_id._cmc_budget_add(vals, self.amount, self.seasonality_id or None)
        return {"type": "ir.actions.act_window_close"}
