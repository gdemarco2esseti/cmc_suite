from collections import defaultdict

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_round

from odoo.addons.cmc_base.models.cmc_utils import month_start, spread

PCT_TOLERANCE = 0.01


class CmcTargetCost(models.Model):
    _name = "cmc.target.cost"
    _inherit = ["cmc.tlc.run.mixin"]
    _description = "Target Costing Sheet"
    _order = "date desc, id desc"
    _check_company_auto = True

    name = fields.Char(required=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    product_id = fields.Many2one("product.product", required=True, check_company=True)
    unit_driver_id = fields.Many2one("cmc.cost.driver", string="Unit Driver", required=True, help="Output measure the unit cost is published per.")
    date = fields.Date(required=True, default=fields.Date.context_today, help="Month of the current cost.")
    target_price = fields.Monetary()
    target_margin_pct = fields.Float(string="Target Margin %", digits=(16, 2))
    non_mfg_cost = fields.Monetary(string="Distribution / Non-Mfg Cost", help="Per unit.")
    assembly_cost = fields.Monetary(help="Part of the allowable cost kept at product level, not deployed.")
    function_ids = fields.One2many("cmc.target.cost.function", "target_id", string="Functions")
    component_ids = fields.One2many("cmc.target.cost.component", "target_id", string="Components")
    matrix_ids = fields.One2many("cmc.target.cost.matrix", "target_id", string="Function-Cost Matrix")
    result_ids = fields.One2many("cmc.target.cost.result", "target_id", string="Deployment", copy=False)
    cost_object_id = fields.Many2one("cmc.cost.object", readonly=True, copy=False)
    allowable_cost = fields.Monetary(readonly=True, copy=False, help="Price x (1 - margin) - non-manufacturing cost.")
    deployable_cost = fields.Monetary(readonly=True, copy=False, help="Allowable cost - assembly cost.")
    current_cost = fields.Monetary(readonly=True, copy=False, help="Unit cost of the product in the registry.")
    cost_gap = fields.Monetary(readonly=True, copy=False, help="Current - allowable.")
    reduction_pct = fields.Float(string="Reduction %", readonly=True, copy=False, digits=(16, 2))
    target_met = fields.Boolean(readonly=True, copy=False)

    _cmc_input_fields = ( "company_id", "product_id", "unit_driver_id", "date", "target_price", "target_margin_pct",
        "non_mfg_cost", "assembly_cost", "function_ids", "component_ids", "matrix_ids",
    )
    _cmc_result_fields = ("result_ids",)
    _cmc_output_fields = (
        "cost_object_id", "allowable_cost", "deployable_cost", "current_cost", "cost_gap", "reduction_pct", "target_met",
    )

    @api.constrains("target_margin_pct")
    def _check_target_margin_pct(self):
        if self.filtered(lambda sheet: not 0 <= sheet.target_margin_pct < 100):
            raise ValidationError(self.env._("The target margin must be at least 0% and below 100%."))

    # ---- engine ---------------------------------------------------------

    def _cmc_compute(self):
        for sheet in self:
            sheet._tlc_compute()

    def _tlc_compute(self):
        month = month_start(self.date)
        registry = self.component_ids.filtered(lambda comp: comp.cost_source == "registry")
        no_product = registry.filtered(lambda comp: not comp.product_id)
        if no_product:
            raise UserError(self.env._("Components priced from the registry need a product: %(names)s", names=", ".join(no_product.mapped("name"))))
        products = self.product_id | registry.product_id
        objects = dict(zip(products.ids, self.env["cmc.cost.object"]._cmc_ensure_objects(products, company=self.company_id), strict=True))
        driver = self.unit_driver_id
        keys = {product: (objects[product.id]._name, objects[product.id].id, driver.id, month) for product in products}
        rates = self._tlc_rates(keys.values())
        if keys[self.product_id] not in rates:
            raise UserError(self.env._("No unit cost published for %(product)s in %(month)s.", product=self.product_id.display_name, month=month))
        missing = registry.filtered(lambda comp: keys[comp.product_id] not in rates)
        if missing:
            raise UserError(self.env._("No unit cost published for the components: %(names)s", names=", ".join(missing.mapped("name"))))
        currency = self.currency_id
        allowable = currency.round(self.target_price * (1 - self.target_margin_pct / 100) - self.non_mfg_cost)
        current = currency.round(rates[keys[self.product_id]].value)
        gap = currency.round(current - allowable)
        cost = {
            comp.id: currency.round(rates[keys[comp.product_id]].value) if comp.cost_source == "registry" else comp.current_cost
            for comp in self.component_ids
        }
        if self.function_ids:
            self._tlc_deploy(allowable - self.assembly_cost, cost)
        self.write({
            "cost_object_id": objects[self.product_id.id].id, "allowable_cost": allowable,
            "deployable_cost": currency.round(allowable - self.assembly_cost), "current_cost": current, "cost_gap": gap,
            "reduction_pct": round(gap / current * 100, 2) if current else 0.0, "target_met": gap <= 0,
        })

    def _tlc_deploy(self, deployable, cost):
        """Function / component targets of the deployable cost (Kaplan Ch11 App); {component id: current cost}."""
        functions, components = self.function_ids, self.component_ids
        if abs(sum(functions.mapped("importance_pct")) - 100) > PCT_TOLERANCE:
            raise UserError(self.env._("The importance of the functions must sum to 100%."))
        share = defaultdict(float)
        for line in self.matrix_ids:
            share[line.component_id.id, line.function_id.id] += line.share_pct
        wrong = components.filtered(lambda comp: abs(sum(share[comp.id, f.id] for f in functions) - 100) > PCT_TOLERANCE)
        if wrong:
            raise UserError(self.env._("The shares of a component across the functions must sum to 100%%: %(names)s", names=", ".join(wrong.mapped("name"))))
        function_cost = {f.id: sum(cost[c.id] * share[c.id, f.id] / 100 for c in components) for f in functions}
        empty = functions.filtered(lambda f: not function_cost[f.id])
        if empty:
            raise UserError(self.env._("No cost is assigned to the functions: %(names)s", names=", ".join(empty.mapped("name"))))
        total = sum(cost.values())
        rounding = self.currency_id.rounding
        function_target = spread(deployable, {f.id: f.importance_pct for f in functions}, rounding)
        importance = {
            c.id: sum(f.importance_pct * cost[c.id] * share[c.id, f.id] / 100 / function_cost[f.id] for f in functions) for c in components
        }
        component_target = spread(deployable, importance, rounding)

        def row(line_type, current, pct, target, **vals):
            cost_pct = current / total * 100 if total else 0.0
            return {
                "target_id": self.id, "line_type": line_type, "importance_pct": round(pct, 2), "current_cost": float_round(current, precision_rounding=rounding),
                "cost_pct": round(cost_pct, 2), "target_cost": target, "gap": float_round(current - target, precision_rounding=rounding),
                "value_index": pct / cost_pct if cost_pct else 0.0, **vals,
            }

        vals_list = [row("function", function_cost[f.id], f.importance_pct, function_target[f.id], function_id=f.id) for f in functions]
        vals_list += [
            row(
                "component", cost[c.id], importance[c.id], component_target[c.id], component_id=c.id, partner_id=c.partner_id.id,
                supplier_target_price=component_target[c.id] if c.purchased else 0.0,
            )
            for c in components
        ]
        # why sudo: users read the deployment, the engine writes it (one create for the whole sheet)
        self.env["cmc.target.cost.result"].sudo().create(vals_list)


class CmcTargetCostFunction(models.Model):
    _name = "cmc.target.cost.function"
    _inherit = ["cmc.run.line.mixin"]
    _description = "Target Cost Function"
    _order = "sequence, id"
    _cmc_run_field = "target_id"

    target_id = fields.Many2one("cmc.target.cost", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="target_id.company_id", store=True, index=True)
    sequence = fields.Integer(default=10)
    name = fields.Char(required=True)
    importance_pct = fields.Float(string="Importance %", digits=(16, 4), help="Customer weight.")


class CmcTargetCostComponent(models.Model):
    _name = "cmc.target.cost.component"
    _inherit = ["cmc.run.line.mixin"]
    _description = "Target Cost Component"
    _order = "sequence, id"
    _check_company_auto = True
    _cmc_run_field = "target_id"

    target_id = fields.Many2one("cmc.target.cost", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="target_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="target_id.currency_id")
    sequence = fields.Integer(default=10)
    name = fields.Char(required=True)
    product_id = fields.Many2one("product.product", check_company=True)
    cost_source = fields.Selection([("manual", "Manual"), ("registry", "Registry")], required=True, default="manual")
    current_cost = fields.Monetary()
    purchased = fields.Boolean()
    partner_id = fields.Many2one("res.partner", string="Supplier")


class CmcTargetCostMatrix(models.Model):
    _name = "cmc.target.cost.matrix"
    _inherit = ["cmc.run.line.mixin"]
    _description = "Target Cost Function-Cost Matrix"
    _order = "id"
    _cmc_run_field = "target_id"

    target_id = fields.Many2one("cmc.target.cost", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="target_id.company_id", store=True, index=True)
    component_id = fields.Many2one("cmc.target.cost.component", required=True, ondelete="cascade", domain="[('target_id', '=', target_id)]")
    function_id = fields.Many2one("cmc.target.cost.function", required=True, ondelete="cascade", domain="[('target_id', '=', target_id)]")
    share_pct = fields.Float(string="Share %", digits=(16, 4), help="Share of the component cost serving the function.")


class CmcTargetCostResult(models.Model):
    _name = "cmc.target.cost.result"
    _inherit = ["cmc.run.result.mixin"]
    _description = "Target Cost Deployment"
    _order = "line_type desc, id"

    target_id = fields.Many2one("cmc.target.cost", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="target_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="target_id.currency_id")
    line_type = fields.Selection([("function", "Function"), ("component", "Component")], required=True)
    function_id = fields.Many2one("cmc.target.cost.function", ondelete="cascade")
    component_id = fields.Many2one("cmc.target.cost.component", ondelete="cascade")
    partner_id = fields.Many2one("res.partner", string="Supplier")
    importance_pct = fields.Float(string="Importance %", digits=(16, 2))
    current_cost = fields.Monetary()
    cost_pct = fields.Float(string="Cost %", digits=(16, 2))
    target_cost = fields.Monetary()
    gap = fields.Monetary()
    value_index = fields.Float(digits=(16, 4), help="Importance % / cost %: above 1 the function is cheap for its worth.")
    supplier_target_price = fields.Monetary()
