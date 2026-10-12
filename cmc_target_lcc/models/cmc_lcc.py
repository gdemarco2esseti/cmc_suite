from collections import defaultdict

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

from odoo.addons.cmc_base.models.cmc_utils import largest_remainder, month_start, nominal_rate, npv, real_rate

BASIS = [("nominal", "Nominal"), ("real", "Real")]
PHASES = [
    ("rd", "R&D"), ("production", "Production"), ("distribution", "Distribution"), ("use", "Use / Service"),
    ("take_back", "Take-back / Disposal"),
]
CATEGORIES = [("usual", "Usual"), ("hidden", "Hidden"), ("liability", "Liability"), ("less_tangible", "Less tangible")]


class CmcLccModel(models.Model):
    _name = "cmc.lcc.model"
    _inherit = ["cmc.tlc.run.mixin"]
    _description = "Life-Cycle Cost Model"
    _order = "id desc"
    _check_company_auto = True

    name = fields.Char(required=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    product_id = fields.Many2one("product.product", check_company=True)
    partner_id = fields.Many2one("res.partner", string="Customer")
    start_year = fields.Integer(default=lambda self: fields.Date.context_today(self).year)
    horizon_years = fields.Integer(default=5)
    discount_rate = fields.Float(string="Discount Rate %", digits=(16, 4))
    inflation_rate = fields.Float(string="Inflation %", digits=(16, 4))
    rate_basis = fields.Selection(BASIS, required=True, default="real")
    flows_basis = fields.Selection(BASIS, required=True, default="real")
    rate_date = fields.Date(help="Month of the driver rates.")
    previous_id = fields.Many2one("cmc.lcc.model", string="Previous Model", check_company=True, copy=False, help="Computed model whose unit cost was in force.")
    baseline_id = fields.Many2one("cmc.lcc.model", string="Baseline", check_company=True, copy=False, help="Computed alternative / do-nothing model.")
    year_ids = fields.One2many("cmc.lcc.year", "model_id", string="Years")
    cost_line_ids = fields.One2many("cmc.lcc.cost.line", "model_id", string="Costs")
    year_result_ids = fields.One2many("cmc.lcc.year.result", "model_id", string="Cash View", copy=False)
    phase_result_ids = fields.One2many("cmc.lcc.phase.result", "model_id", string="Cost View", copy=False)
    effective_rate = fields.Float(string="Effective Rate %", readonly=True, copy=False, digits=(16, 6))
    lc_cost = fields.Monetary(string="LC Cost", readonly=True, copy=False)
    lc_cost_npv = fields.Monetary(string="LC Cost (NPV)", readonly=True, copy=False)
    recurring_cost = fields.Monetary(readonly=True, copy=False)
    non_recurring_cost = fields.Monetary(readonly=True, copy=False)
    lifetime_volume = fields.Float(readonly=True, copy=False, digits="Product Unit")
    lc_unit_cost = fields.Float(string="LC Unit Cost", readonly=True, copy=False, digits=(16, 4))
    lc_revenue = fields.Monetary(string="LC Revenue", readonly=True, copy=False)
    lc_profit = fields.Monetary(string="LC Profit", readonly=True, copy=False)
    lc_profit_npv = fields.Monetary(string="LC Profit (NPV)", readonly=True, copy=False)
    has_breakeven = fields.Boolean(readonly=True, copy=False)
    breakeven_year = fields.Integer(readonly=True, copy=False)
    has_actuals = fields.Boolean(readonly=True, copy=False)
    absorbed_to_date = fields.Monetary(readonly=True, copy=False)
    residual_cost = fields.Monetary(readonly=True, copy=False)
    residual_volume = fields.Float(readonly=True, copy=False, digits="Product Unit")
    residual_unit_cost = fields.Float(readonly=True, copy=False, digits=(16, 4))
    incremental_npv = fields.Monetary(readonly=True, copy=False)

    _cmc_input_fields = ( "company_id", "product_id", "partner_id", "start_year", "horizon_years", "discount_rate", "inflation_rate",
        "rate_basis", "flows_basis", "rate_date", "previous_id", "baseline_id", "year_ids", "cost_line_ids",
    )
    _cmc_result_fields = ("year_result_ids", "phase_result_ids")
    _cmc_output_fields = (
        "effective_rate", "lc_cost", "lc_cost_npv", "recurring_cost", "non_recurring_cost", "lifetime_volume", "lc_unit_cost",
        "lc_revenue", "lc_profit", "lc_profit_npv", "has_breakeven", "breakeven_year", "has_actuals", "absorbed_to_date",
        "residual_cost", "residual_volume", "residual_unit_cost", "incremental_npv",
    )

    @api.constrains("product_id", "partner_id", "horizon_years")
    def _check_object_and_horizon(self):
        for model in self:
            if not (model.product_id or model.partner_id):
                raise ValidationError(self.env._("A life-cycle model needs a product or a customer."))
            if model.horizon_years < 1:
                raise ValidationError(self.env._("The horizon must be at least one year."))

    # ---- engine ---------------------------------------------------------

    def _cmc_compute(self):
        for model in self:
            model._tlc_compute()

    def _tlc_effective_rate(self):
        rate = self.discount_rate / 100
        if self.rate_basis == self.flows_basis:
            return rate
        inflation = self.inflation_rate / 100
        return real_rate(rate, inflation) if self.flows_basis == "real" else nominal_rate(rate, inflation)

    def _tlc_line_amounts(self):
        """{line id: effective cost}: a driver line is quantity x registry rate (snapshotted), an actual year what happened."""
        lines = self.cost_line_ids
        driver_lines = lines.filtered("driver_id")
        if driver_lines and not self.rate_date:
            raise UserError(self.env._("Give the rate month: the model has driver-based cost lines."))
        month = month_start(self.rate_date) if self.rate_date else False
        keys = {line: (line.holder_model, line.holder_id, line.driver_id.id, month) for line in driver_lines}
        rates = self._tlc_rates(keys.values())
        missing = driver_lines.filtered(lambda line: keys[line] not in rates)
        if missing:
            raise UserError(self.env._("No rate published for: %(lines)s", lines=", ".join(missing.mapped("name"))))
        currency = self.currency_id
        snapshot = {
            line.id: {"rate_value": rates[keys[line]].value, "amount": currency.round(line.quantity * rates[keys[line]].value)}
            for line in driver_lines
        }
        self._tlc_write_batch(driver_lines, snapshot)
        actual = {y.year for y in self.year_ids if y.is_actual}
        untyped = lines.filtered(
            lambda line: line.year in actual and not line.actual_amount and not line.actual_zero
            and (snapshot[line.id]["amount"] if line.driver_id else line.amount)
        )
        if untyped:
            raise UserError(self.env._(
                "Type the actual (or confirm it is zero) in an actual year for: %(lines)s", lines=", ".join(untyped.mapped("name")),
            ))
        return {
            line.id: line.actual_amount if line.year in actual
            else currency.round((snapshot[line.id]["amount"] if line.driver_id else line.amount) * line.probability_pct / 100)
            for line in lines
        }

    def _tlc_check_horizon(self):
        horizon = range(self.horizon_years)
        late = self.year_ids.filtered(lambda y: y.year not in horizon).mapped("year")
        late_lines = self.cost_line_ids.filtered(lambda c: c.year not in horizon or c.commit_year not in horizon)
        if late or late_lines:
            raise UserError(self.env._(
                "Years and costs must fall inside the horizon (0 to %(last)s): %(items)s",
                last=self.horizon_years - 1, items=", ".join([str(y) for y in late] + late_lines.mapped("name")),
            ))

    def _tlc_compute(self):
        self._tlc_check_horizon()
        currency, rounding, horizon = self.currency_id, self.currency_id.rounding, self.horizon_years
        years = {y.year: y for y in self.year_ids}
        amounts = self._tlc_line_amounts()
        if self.previous_id | self.baseline_id:
            self._cmc_record_usage(self.previous_id | self.baseline_id)
        rate = self._tlc_effective_rate()
        factor = [1 / (1 + rate) ** t for t in range(horizon)]
        cost, recurring, committed = [0.0] * horizon, [0.0] * horizon, [0.0] * horizon
        by_phase = defaultdict(float)
        by_phase_pv = defaultdict(float)
        for line in self.cost_line_ids:
            amount = amounts[line.id]
            cost[line.year] += amount
            committed[line.commit_year] += amount
            if line.recurrence == "recurring":
                recurring[line.year] += amount
            by_phase[line.phase, line.category] += amount
            by_phase_pv[line.phase, line.category] += amount * factor[line.year]
        volume = [years[t].volume if t in years else 0.0 for t in range(horizon)]
        revenue = [currency.round(volume[t] * years[t].price_unit) if t in years else 0.0 for t in range(horizon)]
        net = [revenue[t] - cost[t] for t in range(horizon)]
        lc_cost, lc_revenue = sum(cost), sum(revenue)
        pv_cost = largest_remainder(sum(c * f for c, f in zip(cost, factor, strict=True)), dict(enumerate(c * f for c, f in zip(cost, factor, strict=True))), rounding)
        pv_phase = largest_remainder(sum(by_phase_pv.values()), by_phase_pv, rounding)
        baseline_net = {r.calendar_year: r.net_flow for r in self.baseline_id.year_result_ids}
        year_vals, cum_net, cum_pv, cum_cost, cum_committed = [], 0.0, 0.0, 0.0, 0.0
        breakeven, seen_negative = False, False
        for t in range(horizon):
            cum_net, cum_pv = cum_net + net[t], cum_pv + net[t] * factor[t]
            cum_cost, cum_committed = cum_cost + cost[t], cum_committed + committed[t]
            seen_negative = seen_negative or cum_net < 0
            if seen_negative and not breakeven and cum_net >= 0:
                breakeven = self.start_year + t
            year_vals.append({
                "model_id": self.id, "year": t, "calendar_year": self.start_year + t, "volume": volume[t], "revenue": revenue[t],
                "cost": cost[t], "recurring_cost": recurring[t], "non_recurring_cost": cost[t] - recurring[t], "net_flow": net[t],
                "discount_factor": factor[t], "pv_cost": pv_cost[t], "pv_net": net[t] * factor[t], "cumulative_net": cum_net,
                "cumulative_pv": cum_pv, "committed_cost": committed[t],
                "cumulative_committed_pct": round(cum_committed / lc_cost * 100, 2) if lc_cost else 0.0,
                "cumulative_incurred_pct": round(cum_cost / lc_cost * 100, 2) if lc_cost else 0.0,
                "is_actual": t in years and years[t].is_actual,
                "incremental_flow": net[t] - baseline_net.get(self.start_year + t, 0.0) if self.baseline_id else 0.0,
            })
        phase_vals = [
            {
                "model_id": self.id, "phase": phase, "category": category, "amount": amount, "pv_amount": pv_phase[phase, category],
                "share_pct": round(amount / lc_cost * 100, 2) if lc_cost else 0.0,
            }
            for (phase, category), amount in by_phase.items()
        ]
        # why sudo: users read both views, the engine writes them (one create each)
        self.env["cmc.lcc.year.result"].sudo().create(year_vals)
        self.env["cmc.lcc.phase.result"].sudo().create(phase_vals)
        lifetime = sum(volume)
        profit_npv = npv(rate, net)
        unit_cost = lc_cost / lifetime if lifetime else 0.0
        self.write({
            "effective_rate": rate * 100, "lc_cost": lc_cost, "lc_cost_npv": npv(rate, cost), "recurring_cost": sum(recurring),
            "non_recurring_cost": lc_cost - sum(recurring), "lifetime_volume": lifetime, "lc_unit_cost": unit_cost,
            "lc_revenue": lc_revenue, "lc_profit": lc_revenue - lc_cost, "lc_profit_npv": profit_npv,
            "has_breakeven": bool(breakeven), "breakeven_year": breakeven or 0,
            "incremental_npv": self._tlc_incremental_npv(rate, net, baseline_net),
            **self._tlc_residual(years, volume, lc_cost, unit_cost),
        })

    def _tlc_incremental_npv(self, rate, net, baseline_net):
        """NPV of model - baseline flows on the common calendar timeline, discounted from the earliest start year."""
        baseline = self.baseline_id
        if not baseline:
            return 0.0
        first = min(self.start_year, baseline.start_year)
        last = max(self.start_year + self.horizon_years, baseline.start_year + baseline.horizon_years)
        own = {self.start_year + t: n for t, n in enumerate(net)}
        return npv(rate, [own.get(cy, 0.0) - baseline_net.get(cy, 0.0) for cy in range(first, last)])

    def _tlc_residual(self, years, volume, lc_cost, unit_cost):
        """Turney Ch8/14: the rate in force is the residual unit cost; actual years absorb cost at the rate in force."""
        actual = {t for t, y in years.items() if y.is_actual}
        previous = self.previous_id
        absorbed = 0.0
        if actual:
            # RN-001: "actual in previous" is matched on the calendar year, never on the index
            previous_actual = {previous.start_year + y.year for y in previous.year_ids if y.is_actual}
            new_actual = {t for t in actual if self.start_year + t not in previous_actual}
            absorbed = (previous.absorbed_to_date if previous else 0.0) + sum(volume[t] for t in new_actual if t < len(volume)) * (
                previous.residual_unit_cost if previous else unit_cost
            )
        residual_volume = sum(v for t, v in enumerate(volume) if t not in actual)
        residual_cost = lc_cost - absorbed
        return {
            "has_actuals": bool(actual), "absorbed_to_date": absorbed, "residual_cost": residual_cost,
            "residual_volume": residual_volume, "residual_unit_cost": residual_cost / residual_volume if residual_volume else 0.0,
        }


class CmcLccYear(models.Model):
    _name = "cmc.lcc.year"
    _inherit = ["cmc.run.line.mixin"]
    _description = "Life-Cycle Year"
    _order = "year"
    _cmc_run_field = "model_id"

    model_id = fields.Many2one("cmc.lcc.model", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="model_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="model_id.currency_id")
    year = fields.Integer(required=True, help="0 = first year of the model.")
    volume = fields.Float(digits="Product Unit")
    price_unit = fields.Monetary()
    is_actual = fields.Boolean(string="Actual")

    _year_uniq = models.UniqueIndex("(model_id, year)", "A model has one volume line per year.")


class CmcLccCostLine(models.Model):
    _name = "cmc.lcc.cost.line"
    _inherit = ["cmc.run.line.mixin"]
    _description = "Life-Cycle Cost Line"
    _order = "year, id"
    _cmc_run_field = "model_id"

    model_id = fields.Many2one("cmc.lcc.model", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="model_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="model_id.currency_id")
    name = fields.Char(required=True)
    phase = fields.Selection(PHASES, required=True, default="production")
    category = fields.Selection(CATEGORIES, required=True, default="usual")
    recurrence = fields.Selection([("recurring", "Recurring"), ("non_recurring", "Non-recurring")], required=True, default="recurring")
    year = fields.Integer()
    commit_year = fields.Integer(help="Year the cost is committed.")
    amount = fields.Monetary(help="Estimate; for a driver line the snapshot of quantity x rate.")
    probability_pct = fields.Float(string="Probability %", default=100.0, digits=(16, 2), help="Below 100: a contingent liability weighs its expected value.")
    actual_amount = fields.Monetary(help="What happened, used in an actual year.")
    actual_zero = fields.Boolean(string="Confirmed Zero", help="The actual is a confirmed zero: the estimated cost did not happen.")
    holder_model = fields.Char()
    holder_id = fields.Many2oneReference(model_field="holder_model")
    driver_id = fields.Many2one("cmc.cost.driver", string="Driver")
    quantity = fields.Float(digits="Product Unit")
    rate_value = fields.Float(readonly=True, copy=False, digits=(16, 6))

    @api.constrains("holder_model", "holder_id", "driver_id")
    def _check_driver_holder(self):
        if self.filtered(lambda line: line.driver_id and not (line.holder_model and line.holder_id)):
            raise ValidationError(self.env._("A driver-based cost line needs the holder of the rate."))


class CmcLccYearResult(models.Model):
    _name = "cmc.lcc.year.result"
    _inherit = ["cmc.run.result.mixin"]
    _description = "Life-Cycle Year Result"
    _order = "year"

    model_id = fields.Many2one("cmc.lcc.model", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="model_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="model_id.currency_id")
    year = fields.Integer()
    calendar_year = fields.Integer()
    volume = fields.Float(digits="Product Unit")
    revenue = fields.Monetary()
    cost = fields.Monetary()
    recurring_cost = fields.Monetary()
    non_recurring_cost = fields.Monetary()
    net_flow = fields.Monetary()
    discount_factor = fields.Float(digits=(16, 6))
    pv_cost = fields.Monetary()
    pv_net = fields.Monetary()
    cumulative_net = fields.Monetary()
    cumulative_pv = fields.Monetary()
    committed_cost = fields.Monetary()
    cumulative_committed_pct = fields.Float(string="Cumulative Committed %", digits=(16, 2))
    cumulative_incurred_pct = fields.Float(string="Cumulative Incurred %", digits=(16, 2))
    is_actual = fields.Boolean(string="Actual")
    incremental_flow = fields.Monetary(help="Net flow - baseline net flow of the same year.")


class CmcLccPhaseResult(models.Model):
    _name = "cmc.lcc.phase.result"
    _inherit = ["cmc.run.result.mixin"]
    _description = "Life-Cycle Phase Result"
    _order = "phase, category"

    model_id = fields.Many2one("cmc.lcc.model", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="model_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="model_id.currency_id")
    phase = fields.Selection(PHASES)
    category = fields.Selection(CATEGORIES)
    amount = fields.Monetary()
    pv_amount = fields.Monetary()
    share_pct = fields.Float(string="Share %", digits=(16, 2))
