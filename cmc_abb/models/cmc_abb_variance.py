from collections import defaultdict

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.fields import Domain
from odoo.tools import float_compare

from odoo.addons.cmc_base.models.cmc_utils import month_end, month_range, month_start

from .cmc_abb_run import QTY
from .cmc_abc_resource import SPENDING_PROFILES

TOTAL_FIELDS = {
    "total_budget_cost": "budget_cost", "total_actual_cost": "actual_cost", "total_assigned_cost": "assigned_cost",
    "total_budgeted_unused_cost": "budgeted_unused_cost", "total_utilisation_variance": "utilisation_variance",
    "total_spending_variance": "spending_variance",
}


class CmcAbbVarianceRun(models.Model):
    _name = "cmc.abb.variance.run"
    _inherit = ["cmc.run.mixin"]
    _description = "ABC Variance Run"
    _order = "date_to desc, id desc"
    _check_company_auto = True
    _check_company_domain = models.check_company_domain_parent_of

    _cmc_input_fields = ("company_id", "abb_run_id", "date_from", "date_to")
    _cmc_result_fields = ("line_ids",)

    name = fields.Char(required=True, default=lambda self: self.env._("ABC variances"))
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    abb_run_id = fields.Many2one(
        "cmc.abb.run", string="ABB Run", required=True, check_company=True, domain="[('state', '=', 'done')]",
        help="Computed activity-based budget the period is measured against.",
    )
    date_from = fields.Date(required=True)
    date_to = fields.Date(required=True)
    total_budget_cost = fields.Monetary(readonly=True, copy=False)
    total_actual_cost = fields.Monetary(readonly=True, copy=False)
    total_assigned_cost = fields.Monetary(readonly=True, copy=False)
    total_budgeted_unused_cost = fields.Monetary(readonly=True, copy=False)
    total_utilisation_variance = fields.Monetary(readonly=True, copy=False, help="Negative: capacity planned for use left idle.")
    total_spending_variance = fields.Monetary(readonly=True, copy=False, help="Budget minus actual: positive is favourable.")
    line_ids = fields.One2many("cmc.abb.variance.line", "run_id")

    @api.constrains("date_from", "date_to")
    def _check_dates(self):
        if any(run.date_to < run.date_from for run in self):
            raise ValidationError(self.env._("The end date cannot precede the start date."))
        if any(run.date_from != month_start(run.date_from) or run.date_to != month_end(run.date_to) for run in self):
            raise ValidationError(self.env._("The period must run from the first to the last day of a month."))

    def _cmc_reset_values(self):
        return {**super()._cmc_reset_values(), **dict.fromkeys(TOTAL_FIELDS, 0.0)}

    # ---- engine ---------------------------------------------------------------

    def _cmc_compute(self):
        self.ensure_one()
        abb = self.abb_run_id
        # raises unless the budget is computed; locks it against a reset while this run is computed
        self._cmc_record_usage(abb)
        source = abb.source_version_id
        if not source.date_from <= self.date_from <= self.date_to <= source.date_to:
            raise UserError(self.env._("The period must lie inside the span of %s.", abb.display_name))
        share = len(month_range(self.date_from, self.date_to)) / len(source._cmc_months())
        results = abb.sudo().resource_result_ids
        actual_demand = self._cmc_actual_demand(abb)
        actual_cost = self._cmc_actual_cost(results.resource_id)
        vals_list = [
            self._cmc_line_vals(row, share, actual_demand.get(row["resource_id"], 0.0), actual_cost.get(row["resource_id"], 0.0))
            for row in results.read([
                "resource_id", "center_id", "element_id", "spending_profile", "demand_qty", "supply_qty", "budgeted_cost", "rate",
            ], load=False)
        ]
        self.env["cmc.abb.variance.line"].sudo().create(vals_list)  # why sudo: results are read-only for users
        self.write({total: sum(vals[name] for vals in vals_list) for total, name in TOTAL_FIELDS.items()})

    def _cmc_actual_demand(self, abb):
        """{resource id: Σ actual facts of the activity driver x usage_per_unit} in the period."""
        sudo = abb.sudo()
        drivers = {a["activity_id"]: a["driver_id"] for a in sudo.activity_result_ids.read(["activity_id", "driver_id"], load=False)}
        # why sudo: actual facts of the run company are engine input, whoever presses Compute
        facts = {
            driver.id: qty for driver, qty in self.env["cmc.measure.quantity"].sudo()._read_group(
                Domain([
                    ("version_id", "=", False), ("company_id", "=", self.company_id.id), ("driver_id", "in", list(set(drivers.values()))),
                    ("date", ">=", month_start(self.date_from)), ("date", "<=", self.date_to),
                ]),
                ["driver_id"], ["quantity:sum"],
            )
        }
        demand = defaultdict(float)
        for line in sudo.usage_line_ids.read(["activity_id", "resource_id", "usage_per_unit"], load=False):
            demand[line["resource_id"]] += facts.get(drivers[line["activity_id"]], 0.0) * line["usage_per_unit"]
        return demand

    def _cmc_actual_cost(self, resources):
        """{resource id: ledger pool of the resource in the period}: the pool rule of the ABC run, not a second one."""
        window = self.env["cmc.abc.run"].new({"company_id": self.company_id.id, "date_from": self.date_from, "date_to": self.date_to})
        cost = defaultdict(float)
        for (res_id, _element), pair in window._cmc_ledger_pools(resources)[0].items():
            cost[res_id] += sum(pair)
        return cost

    def _cmc_line_vals(self, row, share, actual_demand, actual_cost):
        rate, budgeted = row["rate"], row["budgeted_cost"]
        static = budgeted * share
        if row["spending_profile"] == "flexible":
            # two-tier (Kaplan Ch.14): a flexible resource is budgeted at the activity actually performed
            budget_demand, supply, budget = actual_demand, actual_demand, rate * actual_demand
        else:
            budget_demand, supply, budget = row["demand_qty"] * share, row["supply_qty"] * share, static
        assigned = actual_demand * rate
        vals = {
            "run_id": self.id, "resource_id": row["resource_id"], "center_id": row["center_id"], "element_id": row["element_id"],
            "spending_profile": row["spending_profile"], "static_budget_cost": static, "budget_cost": budget,
            "actual_cost": actual_cost, "budget_demand_qty": budget_demand, "actual_demand_qty": actual_demand, "supply_qty": supply,
            "rate": rate, "assigned_cost": assigned, "budgeted_unused_cost": budget - budget_demand * rate,
            "utilisation_variance": (actual_demand - budget_demand) * rate, "spending_variance": budget - actual_cost,
            "abc_variance": assigned - actual_cost,
        }
        # fails loud: assigned + budgeted unused - unexpected utilisation = budget
        gap = vals["assigned_cost"] + vals["budgeted_unused_cost"] - vals["utilisation_variance"] - budget
        if float_compare(gap, 0.0, precision_rounding=self.currency_id.rounding):
            raise UserError(self.env._("The variances of resource %s do not reconcile to its budget.", self.env["cmc.abc.resource"].browse(row["resource_id"]).display_name))
        return vals

    # ---- reports ----------------------------------------------------------------

    def _cmc_three_way(self, groupby=("center_id",)):
        """Actual / ABC (assigned) / ABB (static budget) per group, with the ABC variance."""
        self.ensure_one()
        groups = self.env["cmc.abb.variance.line"]._read_group(
            [("run_id", "=", self.id)], list(groupby), ["actual_cost:sum", "assigned_cost:sum", "static_budget_cost:sum"]
        )
        return [
            {**dict(zip(groupby, key, strict=True)), "actual_cost": actual, "abc_cost": abc, "abb_cost": abb, "abc_variance": abc - actual}
            for *key, actual, abc, abb in groups
        ]

    @api.model
    def _cmc_idle_trend(self, resources, date_from, date_to):
        """[{month, resource, unused_cost, unused_percent}] over the computed variance runs, by month."""
        groups = self.env["cmc.abb.variance.line"]._read_group(
            Domain([("resource_id", "in", resources.ids), ("run_id.state", "=", "done"), ("date_from", ">=", date_from), ("date_from", "<=", date_to)]),
            ["date_from:month", "resource_id"], ["budget_cost:sum", "assigned_cost:sum"],
        )
        return [
            {
                "month": month, "resource": resource, "unused_cost": budget - assigned,
                "unused_percent": (budget - assigned) / budget * 100.0 if budget else 0.0,
            }
            for month, resource, budget, assigned in sorted(groups, key=lambda g: (g[0], g[1].id))
        ]


class CmcAbbVarianceLine(models.Model):
    _name = "cmc.abb.variance.line"
    _inherit = ["cmc.run.result.mixin"]
    _description = "ABC Variance by Resource"
    _order = "run_id, center_id, resource_id"

    run_id = fields.Many2one("cmc.abb.variance.run", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="run_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="run_id.currency_id")
    date_from = fields.Date(related="run_id.date_from", store=True, index=True)
    resource_id = fields.Many2one("cmc.abc.resource", required=True, index=True)
    center_id = fields.Many2one("account.analytic.account", string="Cost Center", index=True)
    element_id = fields.Many2one("cmc.cost.element", string="Cost Element")
    spending_profile = fields.Selection(SPENDING_PROFILES)
    static_budget_cost = fields.Monetary(help="The ABB cost of the resource, pro-rated to the period.")
    budget_cost = fields.Monetary(help="Static budget; a flexible resource is budgeted at the actual activity.")
    actual_cost = fields.Monetary()
    budget_demand_qty = fields.Float(digits=QTY)
    actual_demand_qty = fields.Float(digits=QTY)
    supply_qty = fields.Float(digits=QTY)
    rate = fields.Float(digits=QTY)
    assigned_cost = fields.Monetary(help="Actual activity x standard rate.")
    budgeted_unused_cost = fields.Monetary()
    utilisation_variance = fields.Monetary(help="Negative: capacity planned for use left idle.")
    spending_variance = fields.Monetary(help="Budget minus actual: positive is favourable.")
    abc_variance = fields.Monetary(help="Assigned minus actual.")
