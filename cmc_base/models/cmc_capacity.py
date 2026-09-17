from collections import defaultdict

from odoo import api, fields, models
from odoo.tools import float_round


class CmcCapacityAnalysis(models.Model):
    _name = "cmc.capacity.analysis"
    _description = "Productive Capacity & Cost of Unused Capacity"
    _order = "date_to desc, center_id"
    _check_company_auto = True

    name = fields.Char(compute="_compute_name", store=True)
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company, index=True
    )
    currency_id = fields.Many2one(related="company_id.currency_id")
    center_id = fields.Many2one(
        "account.analytic.account",
        string="Cost Center",
        required=True,
        index=True,
        check_company=True,
        domain="[('cmc_center_type', '!=', False)]",
    )
    date_from = fields.Date(required=True)
    date_to = fields.Date(required=True)
    driver_id = fields.Many2one(
        "cmc.cost.driver", string="Capacity Unit", check_company=True
    )

    # capacity inputs (in driver units, typically hours) — user-entered facts
    capacity_theoretical = fields.Float(digits="Product Unit")
    capacity_practical = fields.Float(digits="Product Unit")
    capacity_actual = fields.Float(digits="Product Unit")

    # derived figures — stored because the row IS period-scoped
    fixed_cost = fields.Monetary(compute="_compute_capacity", store=True)
    fixed_cost_coefficient = fields.Monetary(
        string="Fixed Cost per Practical Unit", compute="_compute_capacity", store=True
    )
    imputed_fixed_cost = fields.Monetary(compute="_compute_capacity", store=True)
    unused_capacity_cost = fields.Monetary(compute="_compute_capacity", store=True)
    utilization_rate = fields.Float(
        string="Utilization %", compute="_compute_capacity", store=True
    )

    _center_period_uniq = models.Constraint(
        "UNIQUE(center_id, date_from, date_to, company_id)",
        "One capacity analysis per cost center and period.",
    )
    _practical_le_theoretical = models.Constraint(
        "CHECK(capacity_theoretical = 0 OR capacity_practical <= capacity_theoretical)",
        "Practical capacity cannot exceed theoretical capacity.",
    )
    _dates_ordered = models.Constraint(
        "CHECK(date_from <= date_to)",
        "The period end date cannot precede its start date.",
    )
    _capacity_non_negative = models.Constraint(
        "CHECK(capacity_theoretical >= 0 AND capacity_practical >= 0 "
        "AND capacity_actual >= 0)",
        "Capacity values cannot be negative.",
    )

    @api.depends("center_id", "date_from", "date_to")
    def _compute_name(self):
        for rec in self:
            rec.name = "%s — %s..%s" % (
                rec.center_id.display_name or "?",
                rec.date_from or "",
                rec.date_to or "",
            )

    def _fixed_costs(self):
        """{record_id: fixed_cost_of_center_in_period}, batched per period.
        Plan-aware: reads the center's costs on whatever analytic plan it lives."""
        result = {}
        by_period = defaultdict(lambda: self.browse())
        for rec in self:
            by_period[(rec.date_from, rec.date_to, rec.company_id.id)] |= rec
        for (df, dt, cid), recs in by_period.items():
            amounts = recs.center_id._cmc_cost_groups(
                df, dt, behavior="fixed",
                company=self.env["res.company"].browse(cid),
            )
            for rec in recs:
                result[rec.id] = amounts.get(rec.center_id.id, 0.0)
        return result

    @api.depends(
        "center_id",
        "date_from",
        "date_to",
        "capacity_practical",
        "capacity_actual",
    )
    def _compute_capacity(self):
        fixed = self._fixed_costs()
        for rec in self:
            rounding = rec.company_id.currency_id.rounding
            fc = fixed.get(rec.id, 0.0)
            prac = rec.capacity_practical
            rec.fixed_cost = fc
            if prac > 0:
                coeff = fc / prac
                imputed = float_round(
                    coeff * rec.capacity_actual, precision_rounding=rounding
                )
            else:
                coeff = 0.0
                imputed = 0.0
            rec.fixed_cost_coefficient = coeff
            rec.imputed_fixed_cost = imputed
            # unused = fixed - imputed by construction → exact conservation
            rec.unused_capacity_cost = float_round(
                fc - imputed, precision_rounding=rounding
            )
            rec.utilization_rate = (
                rec.capacity_actual / prac * 100 if prac else 0.0
            )

    @api.onchange("capacity_actual", "capacity_practical")
    def _warn_overcapacity(self):
        if self.capacity_practical and self.capacity_actual > self.capacity_practical:
            return {
                "warning": {
                    "title": self.env._("Capacity exceeded"),
                    "message": self.env._(
                        "Actual capacity exceeds practical capacity for %(center)s. "
                        "Per the method, review the practical-capacity assumption.",
                        center=self.center_id.display_name,
                    ),
                }
            }

    def action_recompute(self):
        """Refresh derived figures from the underlying analytic lines."""
        self.invalidate_recordset(
            ["fixed_cost", "fixed_cost_coefficient", "imputed_fixed_cost",
             "unused_capacity_cost", "utilization_rate"]
        )
        self._compute_capacity()
        return True
