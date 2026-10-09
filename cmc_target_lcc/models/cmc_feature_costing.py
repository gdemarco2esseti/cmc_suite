from collections import defaultdict

from odoo import fields, models
from odoo.exceptions import UserError

from odoo.addons.cmc_base.models.cmc_utils import month_start


class CmcDesignGuideline(models.Model):
    _name = "cmc.design.guideline"
    _description = "Design Cost Guideline"
    _order = "name, id"
    _check_company_auto = True

    name = fields.Char(required=True)
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company, index=True)
    driver_id = fields.Many2one("cmc.cost.driver", string="Driver", required=True)
    max_quantity = fields.Float(digits="Product Unit", help="Most the design may use of the driver; 0 = no limit.")
    relative_factor = fields.Float(default=1.0, digits=(16, 4), help="Cost of the method relative to the base one (SMT 1.0x, robot 3x, hand 6x).")
    note = fields.Text()
    active = fields.Boolean(default=True)


class CmcFeatureCosting(models.Model):
    _name = "cmc.feature.costing"
    _inherit = ["cmc.tlc.run.mixin"]
    _description = "Feature Costing"
    _order = "id desc"
    _check_company_auto = True

    name = fields.Char(required=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    date = fields.Date(required=True, default=fields.Date.context_today, help="Month of the rates.")
    target_cost_id = fields.Many2one("cmc.target.cost", string="Target Sheet", check_company=True, help="Computed sheet whose allowable cost is the bar.")
    alternative_ids = fields.One2many("cmc.feature.alternative", "costing_id", string="Alternatives")
    variable_ids = fields.One2many("cmc.feature.variable", "costing_id", string="Design Variables")
    result_ids = fields.One2many("cmc.feature.result", "costing_id", string="Results", copy=False)
    violation_ids = fields.One2many("cmc.feature.violation", "costing_id", string="Guideline Violations", copy=False)
    best_alternative_id = fields.Many2one("cmc.feature.alternative", readonly=True, copy=False)

    _cmc_input_fields = ( "company_id", "date", "target_cost_id", "alternative_ids", "variable_ids")
    _cmc_result_fields = ("result_ids", "violation_ids")
    _tlc_outputs = ("best_alternative_id",)

    def _cmc_compute(self):
        for costing in self:
            costing._tlc_compute()

    def _tlc_compute(self):
        if self.target_cost_id:
            self._cmc_record_usage(self.target_cost_id)
        variables = self.variable_ids
        month = month_start(self.date)
        keys = {var: (var.holder_model, var.holder_id, var.driver_id.id, month) for var in variables}
        rates = self._tlc_rates(keys.values())
        missing = variables.filtered(lambda var: keys[var] not in rates)
        if missing:
            raise UserError(self.env._("No rate published for: %(lines)s", lines=", ".join(missing.mapped("name"))))
        currency = self.currency_id
        snapshot = {}
        for var in variables:
            factor = var.guideline_id.relative_factor if var.guideline_id else 1.0
            rate = round(rates[keys[var]].value * factor, 6)
            snapshot[var.id] = {"rate_value": rate, "amount": currency.round(var.quantity * rate)}
        self._tlc_write_batch(variables, snapshot)
        estimated = defaultdict(float)
        quantity = defaultdict(float)
        for var in variables:
            estimated[var.alternative_id.id] += snapshot[var.id]["amount"]
            quantity[var.alternative_id.id, var.driver_id.id] += var.quantity
        alternatives = self.alternative_ids.sorted(lambda alt: (estimated[alt.id], alt.id))
        violations = self._tlc_violations(alternatives, quantity)
        allowable = self.target_cost_id.allowable_cost
        count = defaultdict(int)
        for vals in violations:
            count[vals["alternative_id"]] += 1
        results = []
        for rank, alt in enumerate(alternatives, 1):
            gap = currency.round(estimated[alt.id] - allowable) if self.target_cost_id else 0.0
            results.append({
                "costing_id": self.id, "alternative_id": alt.id, "estimated_cost": currency.round(estimated[alt.id]),
                "allowable_cost": allowable, "gap": gap, "meets_target": gap <= 0, "violation_count": count[alt.id], "rank": rank,
            })
        # why sudo: users read the results, the engine writes them (one create each)
        self.env["cmc.feature.result"].sudo().create(results)
        self.env["cmc.feature.violation"].sudo().create(violations)
        self.write({"best_alternative_id": alternatives[:1].id})

    def _tlc_violations(self, alternatives, quantity):
        guidelines = self.env["cmc.design.guideline"].search([
            ("max_quantity", ">", 0), ("company_id", "in", [False, self.company_id.id]),
        ])
        return [
            {
                "costing_id": self.id, "alternative_id": alt.id, "guideline_id": guideline.id,
                "quantity": quantity[alt.id, guideline.driver_id.id], "max_quantity": guideline.max_quantity,
                "excess": quantity[alt.id, guideline.driver_id.id] - guideline.max_quantity,
            }
            for alt in alternatives
            for guideline in guidelines
            if quantity[alt.id, guideline.driver_id.id] > guideline.max_quantity
        ]


class CmcFeatureAlternative(models.Model):
    _name = "cmc.feature.alternative"
    _inherit = ["cmc.run.line.mixin"]
    _description = "Design Alternative"
    _order = "id"
    _cmc_run_field = "costing_id"

    costing_id = fields.Many2one("cmc.feature.costing", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="costing_id.company_id", store=True, index=True)
    name = fields.Char(required=True)


class CmcFeatureVariable(models.Model):
    _name = "cmc.feature.variable"
    _inherit = ["cmc.run.line.mixin"]
    _description = "Design Variable"
    _order = "id"
    _check_company_auto = True
    _cmc_run_field = "costing_id"

    costing_id = fields.Many2one("cmc.feature.costing", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="costing_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="costing_id.currency_id")
    alternative_id = fields.Many2one("cmc.feature.alternative", required=True, ondelete="cascade", domain="[('costing_id', '=', costing_id)]")
    name = fields.Char(required=True)
    holder_model = fields.Char(required=True)
    holder_id = fields.Many2oneReference(model_field="holder_model", required=True)
    driver_id = fields.Many2one("cmc.cost.driver", string="Driver", required=True)
    quantity = fields.Float(digits="Product Unit")
    guideline_id = fields.Many2one("cmc.design.guideline", string="Guideline", check_company=True)
    rate_value = fields.Float(readonly=True, copy=False, digits=(16, 6))
    amount = fields.Monetary(readonly=True, copy=False)


class CmcFeatureResult(models.Model):
    _name = "cmc.feature.result"
    _inherit = ["cmc.run.result.mixin"]
    _description = "Feature Costing Result"
    _order = "rank, id"

    costing_id = fields.Many2one("cmc.feature.costing", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="costing_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="costing_id.currency_id")
    alternative_id = fields.Many2one("cmc.feature.alternative", required=True, ondelete="cascade")
    estimated_cost = fields.Monetary()
    allowable_cost = fields.Monetary()
    gap = fields.Monetary(help="Estimated - allowable.")
    meets_target = fields.Boolean()
    violation_count = fields.Integer()
    rank = fields.Integer(help="1 = cheapest.")


class CmcFeatureViolation(models.Model):
    _name = "cmc.feature.violation"
    _inherit = ["cmc.run.result.mixin"]
    _description = "Design Guideline Violation"
    _order = "alternative_id, id"

    costing_id = fields.Many2one("cmc.feature.costing", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="costing_id.company_id", store=True, index=True)
    alternative_id = fields.Many2one("cmc.feature.alternative", required=True, ondelete="cascade")
    guideline_id = fields.Many2one("cmc.design.guideline", required=True, ondelete="cascade")
    quantity = fields.Float(digits="Product Unit")
    max_quantity = fields.Float(digits="Product Unit")
    excess = fields.Float(digits="Product Unit")
