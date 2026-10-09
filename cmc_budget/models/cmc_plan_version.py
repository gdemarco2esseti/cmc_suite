from collections import defaultdict
from datetime import date

from odoo import fields, models
from odoo.exceptions import UserError
from odoo.fields import Domain

from odoo.addons.cmc_base.models import cmc_utils
from odoo.addons.cmc_base.models.cmc_utils import month_end, month_start

BVA_AMOUNTS = ("plan_amount:sum", "actual_amount:sum", "prior_actual_amount:sum")


def _pct(variance, plan):
    return variance / abs(plan) * 100.0 if plan else 0.0


class CmcPlanVersion(models.Model):
    _inherit = "cmc.plan.version"

    budget_line_ids = fields.One2many("cmc.budget.line", "version_id", copy=True)
    target_total_cost = fields.Monetary(help="Company total of the cost budget. 0 = no check on approval.")
    target_profit = fields.Monetary()
    alert_threshold = fields.Float(default=10.0, help="Variance percent beyond which a budget vs actual figure is flagged.")

    # ---- engine ---------------------------------------------------------

    def _cmc_compute(self):
        super()._cmc_compute()
        for version in self.filtered("target_total_cost"):
            ((cost,),) = self.env["cmc.budget.line"]._read_group(
                Domain("version_id", "=", version.id) & Domain("kind", "=", "cost"), [], ["amount:sum"]
            )
            if version.currency_id.compare_amounts(-(cost or 0.0), version.target_total_cost):
                raise UserError(
                    self.env._(
                        "The cost lines of %(version)s total %(total)s, not the company total %(target)s.",
                        version=version.display_name, total=-(cost or 0.0), target=version.target_total_cost,
                    )
                )

    # ---- budget building ------------------------------------------------

    def _cmc_budget_add(self, vals, total, seasonality=None):
        """One line per month of the span; the phased figures re-sum to `total`."""
        self.ensure_one()
        months = self._cmc_months()
        parts = cmc_utils.spread(total, self._cmc_phasing_weights(vals, months, seasonality), self.currency_id.rounding)
        return self.env["cmc.budget.line"].create([{**vals, "version_id": self.id, "date": month, "figure": parts[month]} for month in months])

    def _cmc_phasing_weights(self, vals, months, seasonality):
        # why: a fixed cost is annual / 12 (BUD-02); seasonality or planned volume shape variable cost and revenue only
        if vals.get("kind", "cost") == "cost" and vals.get("behavior", "fixed") == "fixed":
            return dict.fromkeys(months, 1.0)
        weights = {}
        if seasonality:
            profile = seasonality._cmc_weights()
            weights = {month: profile.get(month.month, 0.0) for month in months}
        elif vals.get("driver_id"):
            domain = Domain("version_id", "=", self.id) & Domain("driver_id", "=", vals["driver_id"])
            if vals.get("center_id"):
                domain &= Domain("center_id", "=", vals["center_id"])
            planned = dict(self.env["cmc.measure.quantity"]._read_group(domain, ["date:month"], ["quantity:sum"]))
            weights = {month: planned.get(month, 0.0) for month in months}
        if seasonality and not any(weights.values()):
            raise UserError(self.env._("The seasonality profile has no weight in the months of %s.", self.display_name))
        return weights if any(weights.values()) else dict.fromkeys(months, 1.0)

    def _cmc_line_domain(self, date_from=None, date_to=None):
        domain = Domain("version_id", "=", self.id) & Domain("kind", "=", "cost")
        if date_from:
            domain &= Domain("date", ">=", date_from)
        if date_to:
            domain &= Domain("date", "<=", date_to)
        return domain

    def _cmc_center_budget(self, date_from=None, date_to=None):
        self.ensure_one()
        budget = defaultdict(lambda: {"fixed": 0.0, "variable": 0.0, "total": 0.0})
        for center, behavior, amount in self.env["cmc.budget.line"]._read_group(
            self._cmc_line_domain(date_from, date_to), ["center_id", "behavior"], ["amount_company:sum"]
        ):
            budget[center.id][behavior] += -amount
            budget[center.id]["total"] += -amount
        return dict(budget)

    def _cmc_variable_rate(self, center, driver, date_from=None, date_to=None):
        self.ensure_one()
        domain = self._cmc_line_domain(date_from, date_to) & Domain([
            ("center_id", "=", center.id), ("driver_id", "=", driver.id), ("behavior", "=", "variable"),
        ])
        ((cost,),) = self.env["cmc.budget.line"]._read_group(domain, [], ["amount_company:sum"])
        planned = Domain("version_id", "=", self.id) & Domain([("center_id", "=", center.id), ("driver_id", "=", driver.id)])
        if date_from:
            planned &= Domain("date", ">=", date_from)
        if date_to:
            planned &= Domain("date", "<=", date_to)
        ((quantity,),) = self.env["cmc.measure.quantity"]._read_group(planned, [], ["quantity:sum"])
        return -(cost or 0.0) / quantity if quantity else 0.0

    def _cmc_profit_gap(self):
        self.ensure_one()
        totals = dict(self.env["cmc.budget.line"]._read_group(Domain("version_id", "=", self.id), ["kind"], ["amount_company:sum"]))
        revenue, cost = totals.get("revenue", 0.0), -totals.get("cost", 0.0)
        target = self._cmc_to_company_currency(self.target_profit)
        return {"revenue": revenue, "cost": cost, "profit": revenue - cost, "target": target, "gap": target - (revenue - cost)}

    def action_split_annual(self):
        self.ensure_one()
        years = range(self.date_from.year, self.date_to.year + 1)
        if len(years) < 2:
            raise UserError(self.env._("%s spans one year only: there is nothing to split.", self.display_name))
        versions = self.create([
            {
                "name": self.env._("%(name)s %(year)s", name=self.name, year=year), "version_type": "budget", "parent_id": self.id,
                "company_id": self.company_id.id, "currency_id": self.currency_id.id, "currency_rate": self.currency_rate,
                "date_from": max(self.date_from, date(year, 1, 1)), "date_to": min(self.date_to, date(year, 12, 31)),
            }
            for year in years
        ])
        by_year = dict(zip(years, versions))
        lines = self.budget_line_ids
        self.env["cmc.budget.line"].create([
            {**vals, "version_id": by_year[line.date.year].id} for line, vals in zip(lines, lines.copy_data())
        ])
        facts = self.env["cmc.measure.quantity"].search([("version_id", "=", self.id)])
        self.env["cmc.measure.quantity"].create([
            {**vals, "version_id": by_year[fact.date.year].id} for fact, vals in zip(facts, facts.copy_data()) if fact.date.year in by_year
        ])
        return versions

    # ---- forecast -------------------------------------------------------

    def _cmc_rolling_forecast(self, cutoff):
        """Draft forecast = actuals up to the month of `cutoff` + the budget lines of the later months."""
        self.ensure_one()
        cut = month_start(cutoff)
        forecast = self.copy({
            "version_type": "forecast", "budget_line_ids": [],
            "name": self.env._("%(name)s - forecast %(date)s", name=self.name, date=cutoff),
        })
        remainder = self.budget_line_ids.filtered(lambda line: line.date > cut)
        actual_vals = self._cmc_actual_line_vals(month_start(self.date_from), min(month_end(cutoff), self.date_to))
        # why: actuals are company currency, the lines are typed in the version currency (amount_company = actual)
        for vals in actual_vals:
            vals["figure"] = self.currency_id.round(vals["figure"] / self.currency_rate)
        self.env["cmc.budget.line"].create([
            {**vals, "version_id": forecast.id} for vals in actual_vals + remainder.copy_data()
        ])
        return forecast

    def _cmc_actual_line_vals(self, date_from, date_to):
        if date_to < date_from:
            return []
        company = self.company_id
        # why sudo: a certified actual must not depend on the analytic line rules of the caller; scope = the company domain
        costs = self.env["account.analytic.line"].sudo()._cmc_cost_read_group(
            Domain([("company_id", "=", company.id), ("cmc_excluded", "=", False), ("date", ">=", date_from), ("date", "<=", date_to)])
            & Domain("account_id.cmc_center_type", "!=", False),
            ["account_id", "cmc_cost_element_id", "cmc_behavior", "date:month"],
        )
        functions, quantities = self._cmc_semi_variable_context([row for row in costs if row[2] == "semi_variable"], date_from, date_to)
        vals = []
        for center, element, behavior, month, cost in costs:
            variable, driver = 0.0, self.env["cmc.cost.driver"]
            function = functions.get((center, element)) or functions.get((center, self.env["cmc.cost.element"]))
            if behavior == "semi_variable" and function and function.behavior in ("semi_variable", "variable"):
                driver = function.driver_id
                variable = function.variable_rate * quantities.get((center, driver, month), 0.0)
            fixed_behavior = "variable" if behavior == "variable" else "fixed"
            for part, part_behavior in ((cost - variable, fixed_behavior), (variable, "variable")):
                if part:
                    vals.append({
                        "kind": "cost", "center_id": center.id, "element_id": element.id, "behavior": part_behavior,
                        "driver_id": driver.id, "date": month, "figure": part,
                    })
        revenue = self.env["account.move.line"]._cmc_revenue_read_group(company, date_from, date_to, groupby=("date:month",))
        vals += [{"kind": "revenue", "behavior": "variable", "date": month, "figure": amount} for month, amount in revenue if amount]
        return vals

    def _cmc_semi_variable_context(self, rows, date_from, date_to):
        """Cost functions {(center, element): function} and actual quantities {(center, driver, month): qty}, one read each."""
        if not rows:
            return {}, {}
        centers = self.env["account.analytic.account"].union(*(row[0] for row in rows))
        functions = {}
        for function in self.env["cmc.cost.function"].search([("company_id", "=", self.company_id.id), ("center_id", "in", centers.ids)]):
            functions.setdefault((function.center_id, function.element_id), function)
        drivers = self.env["cmc.cost.driver"].union(*(function.driver_id for function in functions.values()))
        quantities = {
            (center, driver, month): quantity
            for center, driver, month, quantity in self.env["cmc.measure.quantity"]._read_group(
                Domain([
                    ("version_id", "=", False), ("company_id", "=", self.company_id.id), ("center_id", "in", centers.ids),
                    ("driver_id", "in", drivers.ids), ("date", ">=", date_from), ("date", "<=", date_to),
                ]),
                ["center_id", "driver_id", "date:month"], ["quantity:sum"],
            )
        }
        return functions, quantities

    # ---- budget vs actual -----------------------------------------------

    def _cmc_bva_summary(self, date_to, groupby=("center_id",)):
        """Month and YTD plan / actual / variance per group, from one grouped read of the BvA view."""
        self.ensure_one()
        month = month_start(date_to)
        ytd_from = max(date(date_to.year, 1, 1), month_start(self.date_from))
        domain = Domain("version_id", "=", self.id) & Domain("date", ">=", ytd_from) & Domain("date", "<=", month)
        groups = defaultdict(lambda: dict.fromkeys(("plan_month", "actual_month", "plan_ytd", "actual_ytd", "prior_ytd"), 0.0))
        for *key, row_month, plan, actual, prior in self.env["cmc.budget.bva"]._read_group(domain, [*groupby, "date:month"], BVA_AMOUNTS):
            totals = groups[tuple(key)]
            totals["plan_ytd"] += plan
            totals["actual_ytd"] += actual
            totals["prior_ytd"] += prior
            if row_month == month:
                totals["plan_month"] += plan
                totals["actual_month"] += actual
        rows = []
        for key, totals in groups.items():
            row = {name: value or False for name, value in zip(groupby, key)}
            for period in ("month", "ytd"):
                variance = totals[f"actual_{period}"] - totals[f"plan_{period}"]
                row |= {
                    f"plan_{period}": totals[f"plan_{period}"], f"actual_{period}": totals[f"actual_{period}"],
                    f"variance_{period}": variance, f"variance_{period}_pct": _pct(variance, totals[f"plan_{period}"]),
                    f"alert_{period}": abs(_pct(variance, totals[f"plan_{period}"])) > self.alert_threshold,
                }
            rows.append(row | {"prior_ytd": totals["prior_ytd"]})
        return rows
