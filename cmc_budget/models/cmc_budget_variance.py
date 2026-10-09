from collections import defaultdict

from odoo import api, fields, models
from odoo.exceptions import ValidationError
from odoo.fields import Domain

from odoo.addons.cmc_base.models.cmc_utils import month_start


class CmcBudgetVarianceRun(models.Model):
    _name = "cmc.budget.variance.run"
    _inherit = ["cmc.run.mixin"]
    _description = "Budget Variance Run"
    _order = "date_to desc, id desc"
    _check_company_auto = True

    _cmc_input_fields = ("company_id", "version_id", "date_from", "date_to", "input_ids")
    _cmc_result_fields = ("line_ids",)

    name = fields.Char(required=True, default=lambda self: self.env._("Budget variances"))
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    version_id = fields.Many2one(
        "cmc.plan.version", required=True, check_company=True, domain="[('version_type', 'in', ('budget', 'forecast'))]",
    )
    date_from = fields.Date(required=True)
    date_to = fields.Date(required=True)
    input_ids = fields.One2many("cmc.budget.variance.input", "run_id", copy=True)
    line_ids = fields.One2many("cmc.budget.variance.line", "run_id")

    @api.constrains("date_from", "date_to")
    def _check_dates_ordered(self):
        if any(run.date_to < run.date_from for run in self):
            raise ValidationError(self.env._("The end date cannot precede the start date."))

    def _cmc_compute(self):
        self.ensure_one()
        # raises unless the version is approved; locks it against a reset while this run is computed
        self._cmc_record_usage(self.version_id)
        budget = self._cmc_budget_groups()
        centers = self.env["account.analytic.account"].union(*(center for center, _element in budget))
        actual_cost = centers._cmc_cost_groups(self.date_from, self.date_to, by_element=True, company=self.company_id)
        Center, Element = self.env["account.analytic.account"], self.env["cmc.cost.element"]
        keys = set(budget) | {(Center.browse(cid), Element.browse(eid)) for cid, eid in actual_cost}
        drivers = self.env["cmc.cost.driver"].union(*(entry["driver"] for entry in budget.values()))
        planned, actual = self._cmc_quantities(centers, drivers)
        allowed = {(line.center_id, line.driver_id): line.allowed_qty for line in self.input_ids}
        practical = self._cmc_practical_capacity(centers, drivers)
        no_entry = {"fixed": 0.0, "variable": 0.0, "driver": self.env["cmc.cost.driver"]}
        self.env["cmc.budget.variance.line"].create([
            self._cmc_line_vals(
                center, element, budget.get((center, element), no_entry), -actual_cost.get((center.id, element.id), 0.0),
                planned, actual, allowed, practical,
            )
            for center, element in keys
        ])

    def _cmc_budget_groups(self):
        domain = Domain([
            ("version_id", "=", self.version_id.id), ("kind", "=", "cost"), ("center_id", "!=", False),
            ("date", ">=", month_start(self.date_from)), ("date", "<=", self.date_to),
        ])
        budget = defaultdict(lambda: {"fixed": 0.0, "variable": 0.0, "driver": self.env["cmc.cost.driver"]})
        for center, element, driver, behavior, amount in self.env["cmc.budget.line"]._read_group(
            domain, ["center_id", "element_id", "driver_id", "behavior"], ["amount_company:sum"]
        ):
            entry = budget[(center, element)]
            entry[behavior] += amount
            entry["driver"] = entry["driver"] or driver
        return budget

    def _cmc_quantities(self, centers, drivers):
        """({(center, driver): planned qty}, {(center, driver): actual qty}) in one grouped read."""
        domain = Domain([
            ("company_id", "=", self.company_id.id), ("center_id", "in", centers.ids), ("driver_id", "in", drivers.ids),
            ("date", ">=", month_start(self.date_from)), ("date", "<=", self.date_to), ("version_id", "in", [False, self.version_id.id]),
        ])
        planned, actual = defaultdict(float), defaultdict(float)
        for center, driver, version, quantity in self.env["cmc.measure.quantity"]._read_group(
            domain, ["center_id", "driver_id", "version_id"], ["quantity:sum"]
        ):
            (planned if version else actual)[(center, driver)] += quantity
        return planned, actual

    def _cmc_practical_capacity(self, centers, drivers):
        practical = defaultdict(float)
        for capacity in self.env["cmc.capacity.analysis"].search([
            ("company_id", "=", self.company_id.id), ("center_id", "in", centers.ids), ("driver_id", "in", drivers.ids),
            ("date_from", "<=", self.date_from), ("date_to", ">=", self.date_to),
        ]):
            practical[(capacity.center_id, capacity.driver_id)] += capacity.capacity_practical
        return practical

    def _cmc_line_vals(self, center, element, entry, actual_amount, planned, actual, allowed, practical):
        fixed, variable, driver = entry["fixed"], entry["variable"], entry["driver"]
        key = (center, driver)
        planned_qty, actual_qty = planned[key], actual[key]
        allowed_qty = allowed.get(key, actual_qty) if driver else 0.0
        # variable and fixed standard rate per planned unit, positive figures
        v = -variable / planned_qty if planned_qty else 0.0
        f = -fixed / planned_qty if planned_qty else 0.0
        flexible = fixed - v * actual_qty if planned_qty else fixed + variable
        capacity_qty = practical[key] if fixed else 0.0
        rate = -fixed / capacity_qty if capacity_qty else 0.0
        return {
            "run_id": self.id, "center_id": center.id, "element_id": element.id, "driver_id": driver.id,
            "planned_qty": planned_qty, "actual_qty": actual_qty, "allowed_qty": allowed_qty,
            "budget_fixed": fixed, "budget_variable": variable, "flexible_amount": flexible, "actual_amount": actual_amount,
            "spending_variance": actual_amount - flexible,
            "efficiency_variance": v * (allowed_qty - actual_qty),
            "volume_variance": f * (allowed_qty - planned_qty),
            "practical_qty": capacity_qty, "capacity_rate": rate,
            "capacity_assigned": -rate * actual_qty,
            "capacity_unused_budgeted": -rate * (capacity_qty - planned_qty) if rate else 0.0,
            "capacity_unused_unexpected": -rate * (planned_qty - actual_qty) if rate else 0.0,
        }


class CmcBudgetVarianceInput(models.Model):
    _name = "cmc.budget.variance.input"
    _inherit = ["cmc.run.line.mixin"]
    _description = "Budget Variance Input"
    _check_company_auto = True

    run_id = fields.Many2one("cmc.budget.variance.run", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="run_id.company_id", store=True, index=True)
    center_id = fields.Many2one("account.analytic.account", required=True, check_company=True, ondelete="restrict")
    driver_id = fields.Many2one("cmc.cost.driver", required=True, check_company=True, ondelete="restrict")
    allowed_qty = fields.Float(
        digits="Product Unit", help="Quantity allowed for the actual output; missing = the actual quantity.",
    )

    _run_center_driver_uniq = models.UniqueIndex("(run_id, center_id, driver_id)", "One allowed quantity per center and driver.")


class CmcBudgetVarianceLine(models.Model):
    _name = "cmc.budget.variance.line"
    _inherit = ["cmc.run.result.mixin"]
    _description = "Budget Variance Result"
    _order = "run_id, center_id, element_id"

    run_id = fields.Many2one("cmc.budget.variance.run", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="run_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="run_id.currency_id")
    # restrict: a cascade would delete the results of a computed run past its lock
    center_id = fields.Many2one("account.analytic.account", required=True, ondelete="restrict", index=True)
    element_id = fields.Many2one("cmc.cost.element", ondelete="restrict")
    driver_id = fields.Many2one("cmc.cost.driver", ondelete="restrict")
    planned_qty = fields.Float(digits="Product Unit")
    actual_qty = fields.Float(digits="Product Unit")
    allowed_qty = fields.Float(digits="Product Unit")
    practical_qty = fields.Float(digits="Product Unit")
    budget_fixed = fields.Monetary()
    budget_variable = fields.Monetary()
    flexible_amount = fields.Monetary()
    actual_amount = fields.Monetary()
    spending_variance = fields.Monetary()
    efficiency_variance = fields.Monetary()
    volume_variance = fields.Monetary()
    capacity_rate = fields.Float(digits="Product Price")
    capacity_assigned = fields.Monetary()
    capacity_unused_budgeted = fields.Monetary()
    capacity_unused_unexpected = fields.Monetary()
