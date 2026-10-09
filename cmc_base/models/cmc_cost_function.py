from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.fields import Domain

from . import cmc_utils


class CmcCostFunction(models.Model):
    _name = "cmc.cost.function"
    _description = "Cost Function (K + v*Q)"
    _order = "center_id, name"
    _check_company_auto = True
    _check_company_domain = models.check_company_domain_parent_of

    name = fields.Char(required=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    center_id = fields.Many2one("account.analytic.account", string="Cost Center", required=True, index=True, check_company=True)
    element_id = fields.Many2one("cmc.cost.element", string="Cost Element", check_company=True)
    driver_id = fields.Many2one("cmc.cost.driver", required=True, check_company=True)
    behavior = fields.Selection(
        selection=[("fixed", "Fixed"), ("variable", "Variable"), ("semi_variable", "Semi-variable"), ("step", "Step")],
        required=True,
        default="semi_variable",
    )
    fixed_amount = fields.Monetary(string="Fixed Cost per Month (K)")
    variable_rate = fields.Float(string="Variable Rate (v)", digits="Product Price")
    range_min = fields.Float(string="Relevant Range Min", help="0 = no bound.")
    range_max = fields.Float(string="Relevant Range Max", help="0 = no bound.")
    method = fields.Selection(
        selection=[("manual", "Manual"), ("high_low", "High-low"), ("ols", "Least squares")],
        required=True,
        default="manual",
    )
    r2 = fields.Float(string="R2", readonly=True)
    date_from = fields.Date(string="History From")
    date_to = fields.Date(string="History To")
    step_ids = fields.One2many("cmc.cost.function.step", "function_id", string="Steps")

    @api.constrains("range_min", "range_max")
    def _check_range_ordered(self):
        for fn in self:
            if fn.range_max and fn.range_max < fn.range_min:
                raise ValidationError(self.env._("The relevant range maximum cannot be below its minimum."))

    @api.constrains("step_ids")
    def _check_steps_not_overlapping(self):
        for fn in self:
            steps = fn.step_ids.sorted("qty_from")
            for prev, step in zip(steps, steps[1:]):
                if step.qty_from < prev.qty_to:
                    raise ValidationError(
                        self.env._("Steps of %(name)s overlap: a quantity must fall in one step only.", name=fn.name)
                    )

    def _cmc_evaluate(self, quantity):
        self.ensure_one()
        if self.behavior == "step":
            for step in self.step_ids:
                if step.qty_from <= quantity < step.qty_to:
                    return step.amount
            raise UserError(self.env._("No step of %(name)s covers the quantity %(qty)s.", name=self.name, qty=quantity))
        fixed = self.fixed_amount if self.behavior in ("fixed", "semi_variable") else 0.0
        variable = self.variable_rate * quantity if self.behavior in ("variable", "semi_variable") else 0.0
        return fixed + variable

    def _cmc_range_warning(self, quantity):
        self.ensure_one()
        if (self.range_min and quantity < self.range_min) or (self.range_max and quantity > self.range_max):
            return self.env._(
                "Quantity %(qty)s is outside the relevant range of %(name)s (%(low)s - %(high)s).",
                qty=quantity, name=self.name, low=self.range_min, high=self.range_max,
            )
        return False

    def _cmc_history(self):
        """[(quantity, cost)] of the months having both, in two grouped reads."""
        self.ensure_one()
        if not (self.date_from and self.date_to):
            raise UserError(self.env._("Set the history span of %(name)s before estimating.", name=self.name))
        col = self.center_id.plan_id._column_name()
        # why sudo: the estimate reads the cost base of the company whatever the caller's
        # line rules show (same scope as _cmc_cost_groups)
        costs = self.env["account.analytic.line"].sudo()._cmc_cost_read_group(
            Domain(col, "=", self.center_id.id) & Domain("cmc_excluded", "=", False)
            # why: relocation postings move cost between centers, they are not behavior evidence
            & self.env["account.analytic.line"]._cmc_relocation_domain(include=False)
            & Domain("company_id", "in", [False, self.company_id.id])
            & Domain("date", ">=", cmc_utils.month_start(self.date_from)) & Domain("date", "<=", self.date_to)
            & (Domain("cmc_cost_element_id", "=", self.element_id.id) if self.element_id else Domain.TRUE),
            groupby=["date:month"],
        )
        quantities = self.env["cmc.measure.quantity"]._read_group(
            Domain("version_id", "=", False) & Domain("driver_id", "=", self.driver_id.id)
            & Domain("center_id", "=", self.center_id.id) & Domain("company_id", "=", self.company_id.id)
            & Domain("date", ">=", cmc_utils.month_start(self.date_from)) & Domain("date", "<=", self.date_to),
            groupby=["date:month"],
            aggregates=["quantity:sum"],
        )
        cost_by_month = dict(costs)
        return [(qty, cost_by_month[month]) for month, qty in quantities if month in cost_by_month]

    def action_estimate(self):
        self.check_access("write")
        for fn in self:
            if fn.method == "manual":
                raise UserError(self.env._("%(name)s is a manual function: there is nothing to estimate.", name=fn.name))
            history = fn._cmc_history()
            if len(history) < 2:
                raise UserError(self.env._("%(name)s needs at least two months with both cost and quantity.", name=fn.name))
            try:
                if fn.method == "ols":
                    fixed, rate, r2 = cmc_utils.ols_fit(history)
                else:
                    (fixed, rate), r2 = cmc_utils.high_low(history), 0.0
            except ValueError as err:
                raise UserError(self.env._("The history of %(name)s cannot separate K from v: %(err)s", name=fn.name, err=err)) from err
            fn.write({"fixed_amount": fixed, "variable_rate": rate, "r2": r2})
        return True


class CmcCostFunctionStep(models.Model):
    _name = "cmc.cost.function.step"
    _description = "Cost Function Step"
    _order = "function_id, qty_from"

    function_id = fields.Many2one("cmc.cost.function", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="function_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="function_id.currency_id")
    qty_from = fields.Float(string="From (included)", digits="Product Unit")
    qty_to = fields.Float(string="To (excluded)", digits="Product Unit")
    amount = fields.Monetary(string="Cost")

    _qty_ordered = models.Constraint("CHECK(qty_to > qty_from)", "A step must end above its start.")
