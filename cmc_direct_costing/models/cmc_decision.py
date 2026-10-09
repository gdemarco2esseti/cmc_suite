from collections import defaultdict

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare

from odoo.addons.cmc_base.models import cmc_utils
from odoo.addons.cmc_base.models.cmc_run_mixin import CMC_RUN_STATE

DECISION_TYPES = [
    ("make_or_buy", "Make or Buy"),
    ("drop_line", "Eliminate Product Line"),
    ("special_order", "Quote / Special Order"),
    ("replace", "Replace Machine"),
    ("generic", "Generic Alternatives"),
    ("mix", "Product Mix"),
    ("investment", "Investment"),
]
BASIS = [("nominal", "Nominal"), ("real", "Real")]
VERDICTS = [("current", "Current"), ("alternative", "Alternative"), ("indifferent", "Indifferent")]
# written by Compute only, cleared by Reset
OUTPUTS = (
    "current_total", "alternative_total", "differential", "verdict", "verdict_label", "has_indifference",
    "indifference_qty", "activity_cost", "idle_burden", "sustaining_burden", "quote_cost", "quote_margin",
    "quote_margin_pct", "mix_total_mc", "mix_total_throughput", "mix_result", "resource_used",
    "effective_rate", "npv", "has_irr", "irr", "irr_note", "has_payback", "payback_years", "has_discounted_payback",
    "discounted_payback_years", "roi", "cbr",
)


class CmcDecision(models.Model):
    _name = "cmc.decision"
    _inherit = ["cmc.run.mixin"]
    _description = "Decision (differential analysis)"
    _order = "date desc, id desc"
    _check_company_auto = True

    name = fields.Char(required=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    date = fields.Date(required=True, default=fields.Date.context_today)
    decision_type = fields.Selection(DECISION_TYPES, required=True, default="generic")
    # ---- inputs
    line_ids = fields.One2many("cmc.decision.line", "decision_id", string="Lines")
    activity_ids = fields.One2many("cmc.decision.activity", "decision_id", string="Activities")
    mix_line_ids = fields.One2many("cmc.decision.mix.line", "decision_id", string="Products")
    cash_flow_ids = fields.One2many("cmc.decision.cash.flow", "decision_id", string="Cash Flows")
    volume = fields.Float(digits="Product Unit")
    buy_price_unit = fields.Monetary()
    opportunity_cost = fields.Monetary()
    quote_price = fields.Monetary()
    target_margin_pct = fields.Float(string="Target Margin %", digits=(16, 2))
    capital_charge = fields.Monetary()
    include_idle = fields.Boolean(string="Include Idle Capacity")
    include_sustaining = fields.Boolean(string="Include Sustaining Cost")
    idle_burden_pct = fields.Float(string="Idle Burden %", digits=(16, 2))
    sustaining_burden_pct = fields.Float(string="Sustaining Burden %", digits=(16, 2))
    resource_available = fields.Float(digits="Product Unit")
    fixed_cost = fields.Monetary()
    mix_ranking = fields.Selection(
        [("contribution", "Contribution per resource unit"), ("throughput", "Throughput per resource unit (TOC)")],
        default="contribution", required=True,
    )
    discount_rate = fields.Float(string="Discount Rate %", digits=(16, 4))
    inflation_rate = fields.Float(string="Inflation %", digits=(16, 4))
    tax_rate = fields.Float(string="Tax Rate %", digits=(16, 4))
    rate_basis = fields.Selection(BASIS, default="nominal", required=True)
    flows_basis = fields.Selection(BASIS, default="nominal", required=True)
    # ---- outputs
    mix_result_ids = fields.One2many("cmc.decision.mix.result", "decision_id", copy=False)
    cash_flow_result_ids = fields.One2many("cmc.decision.cash.flow.result", "decision_id", copy=False)
    current_total = fields.Monetary(readonly=True, copy=False)
    alternative_total = fields.Monetary(readonly=True, copy=False)
    differential = fields.Monetary(readonly=True, copy=False, help="Alternative - current.")
    verdict = fields.Selection(VERDICTS, readonly=True, copy=False)
    verdict_label = fields.Char(readonly=True, copy=False)
    has_indifference = fields.Boolean(readonly=True, copy=False)
    indifference_qty = fields.Float(readonly=True, copy=False, digits="Product Unit")
    activity_cost = fields.Monetary(readonly=True, copy=False)
    idle_burden = fields.Monetary(readonly=True, copy=False)
    sustaining_burden = fields.Monetary(readonly=True, copy=False)
    quote_cost = fields.Monetary(readonly=True, copy=False)
    quote_margin = fields.Monetary(readonly=True, copy=False)
    quote_margin_pct = fields.Float(readonly=True, copy=False, digits=(16, 2))
    mix_total_mc = fields.Monetary(readonly=True, copy=False)
    mix_total_throughput = fields.Monetary(readonly=True, copy=False)
    mix_result = fields.Monetary(string="Mix Profit", readonly=True, copy=False, help="Total contribution - fixed cost.")
    resource_used = fields.Float(readonly=True, copy=False, digits="Product Unit")
    effective_rate = fields.Float(readonly=True, copy=False, digits=(16, 6))
    npv = fields.Monetary(readonly=True, copy=False)
    has_irr = fields.Boolean(readonly=True, copy=False)
    irr = fields.Float(readonly=True, copy=False, digits=(16, 6))
    irr_note = fields.Char(readonly=True, copy=False)
    has_payback = fields.Boolean(readonly=True, copy=False)
    payback_years = fields.Float(readonly=True, copy=False, digits=(16, 6))
    has_discounted_payback = fields.Boolean(readonly=True, copy=False)
    discounted_payback_years = fields.Float(readonly=True, copy=False, digits=(16, 6))
    roi = fields.Float(readonly=True, copy=False, digits=(16, 6))
    cbr = fields.Float(readonly=True, copy=False, digits=(16, 4))

    _cmc_input_fields = ( "company_id", "date", "decision_type", "line_ids", "activity_ids", "mix_line_ids", "cash_flow_ids",
        "volume", "buy_price_unit", "opportunity_cost", "quote_price", "target_margin_pct", "capital_charge",
        "include_idle", "include_sustaining", "idle_burden_pct", "sustaining_burden_pct", "resource_available",
        "fixed_cost", "mix_ranking", "discount_rate", "inflation_rate", "tax_rate", "rate_basis", "flows_basis",
    )
    _cmc_result_fields = ("mix_result_ids", "cash_flow_result_ids")

    def write(self, vals):
        if not self._cmc_state_unlocked() and not set(OUTPUTS).isdisjoint(vals):
            raise UserError(self.env._("The outcome of a decision is written by Compute only."))
        return super().write(vals)

    def _cmc_reset_values(self):
        return {**super()._cmc_reset_values(), **dict.fromkeys(OUTPUTS, False)}

    # ---- engine ---------------------------------------------------------

    def _cmc_compute(self):
        for decision in self:
            if decision.decision_type == "mix":
                decision._cmc_compute_mix()
            elif decision.decision_type == "investment":
                decision._cmc_compute_investment()
            elif decision.decision_type == "special_order":
                decision._cmc_compute_quote()
            else:
                decision._cmc_compute_columns()

    def _cmc_verdict_labels(self):
        """(alternative, current) wording of the verdict; the literals sit here for the extractor."""
        return {
            "make_or_buy": (self.env._("Buy"), self.env._("Make")),
            "drop_line": (self.env._("Eliminate line"), self.env._("Keep line")),
            "special_order": (self.env._("Accept order"), self.env._("Reject order")),
            "replace": (self.env._("Replace"), self.env._("Keep machine")),
            "investment": (self.env._("Invest"), self.env._("Do not invest")),
            "generic": (self.env._("Alternative"), self.env._("Current")),
        }[self.decision_type]

    def _cmc_finish(self, current, alternative, verdict=None, **extra):
        """Write the column totals, the sign-based verdict (currency step) and the type outputs."""
        currency = self.currency_id
        differential = currency.round(alternative - current)
        if verdict is None:
            sign = currency.compare_amounts(differential, 0.0)
            verdict = "alternative" if sign > 0 else "current" if sign < 0 else "indifferent"
        alt_label, cur_label = self._cmc_verdict_labels()
        label = {"alternative": alt_label, "current": cur_label, "indifferent": self.env._("Indifferent")}[verdict]
        self.write({
            "current_total": current, "alternative_total": alternative, "differential": differential,
            "verdict": verdict, "verdict_label": label, **extra,
        })

    def _cmc_price_activities(self):
        """Snapshot qty x rate of every activity line; {side: amount}. One rate read, one usage call."""
        self.ensure_one()
        activities = self.activity_ids
        if not activities:
            return {}
        # rates of the decision company and month only, whatever the producing run
        rates = self.env["cmc.rate"].search([
            ("holder_model", "in", activities.mapped("holder_model")),
            ("holder_id", "in", activities.mapped("holder_id")),
            ("driver_id", "in", activities.driver_id.ids),
            ("component", "=", "total"),
            ("date", "=", cmc_utils.month_start(self.date)),
            ("company_id", "=", self.company_id.id),
            ("origin_model", "!=", "cmc.plan.version"),
        ], order="id desc")
        found = {}
        for rate in rates:
            found.setdefault((rate.holder_model, rate.holder_id, rate.driver_id.id), rate)
        keys = {act: (act.holder_model, act.holder_id, act.driver_id.id) for act in activities}
        missing = activities.filtered(lambda act: keys[act] not in found)
        if missing:
            raise UserError(self.env._("No rate published for: %(lines)s", lines=", ".join(missing.mapped("name"))))
        origins = defaultdict(set)
        for rate in found.values():
            origins[rate.origin_model].add(rate.origin_id)
        for model, ids in origins.items():
            # why sudo: the producer is read whatever the user's rules show; the usage pins it computed
            self._cmc_record_usage(self.env[model].sudo().browse(ids))
        totals = defaultdict(float)
        for act in activities:
            amount = self.currency_id.round(act.quantity * found[keys[act]].value)
            # why sudo: snapshot of the engine; a user-bound write would check access once per line
            act.sudo().write({"rate_value": found[keys[act]].value, "amount": amount})
            totals[act.side] += amount
        return totals

    # generic / replace / drop_line / make_or_buy
    def _cmc_compute_columns(self):
        derive = self.decision_type in ("drop_line", "make_or_buy")
        current = alternative = 0.0
        for line in self.line_ids.filtered(lambda ln: not ln.sunk):
            sign = 1 if line.line_type == "revenue" else -1
            typed = line.amount_alternative
            # the alternative column is derived: revenue goes, avoidable (or variable) cost goes, the rest stays
            alt = (0.0 if line.line_type == "revenue" or line._cmc_is_avoidable() else line.amount_current) if derive else typed
            current += sign * line.amount_current
            alternative += sign * alt
        activities = self._cmc_price_activities()
        current -= activities.get("current", 0.0)
        alternative -= activities.get("alternative", 0.0)
        extra = {}
        if self.decision_type == "make_or_buy":
            current -= self.opportunity_cost
            alternative -= self.buy_price_unit * self.volume
            extra = self._cmc_indifference(activities)
        self._cmc_finish(current, alternative, **extra)

    def _cmc_indifference(self, activities):
        """Q* = (CFr + A_make - A_buy) / (Pe - v), activity costs treated as fixed."""
        costs = self.line_ids.filtered(lambda ln: ln.line_type == "cost" and not ln.sunk)
        variable = sum(costs.filtered(lambda ln: ln.cost_behavior == "variable").mapped("amount_current"))
        unit_variable = variable / self.volume if self.volume else 0.0
        fixed_avoidable = sum(costs.filtered(lambda ln: ln.cost_behavior == "fixed" and ln._cmc_is_avoidable()).mapped("amount_current"))
        qty = None
        if self.buy_price_unit > unit_variable:
            qty = cmc_utils.indifference_qty(
                fixed_avoidable + self.opportunity_cost + activities.get("current", 0.0), unit_variable, activities.get("alternative", 0.0), self.buy_price_unit
            )
        return {"has_indifference": qty is not None, "indifference_qty": qty or 0.0}

    def _cmc_compute_quote(self):
        own = sum(ln.amount_alternative for ln in self.line_ids if ln.line_type == "cost" and not ln.sunk)
        activity_cost = sum(self._cmc_price_activities().values())
        idle = activity_cost * self.idle_burden_pct / 100 if self.include_idle else 0.0
        sustaining = activity_cost * self.sustaining_burden_pct / 100 if self.include_sustaining else 0.0
        cost = self.currency_id.round(own + activity_cost + idle + sustaining + self.capital_charge)
        margin = self.currency_id.round(self.quote_price - cost)
        pct = margin / self.quote_price * 100 if self.quote_price else 0.0
        accept = margin > 0 and float_compare(pct, self.target_margin_pct, precision_digits=2) >= 0
        self._cmc_finish(
            0.0, margin, verdict="alternative" if accept else "current", activity_cost=activity_cost,
            idle_burden=idle, sustaining_burden=sustaining, quote_cost=cost, quote_margin=margin, quote_margin_pct=pct,
        )

    def _cmc_compute_mix(self):
        if self.resource_available <= 0:
            raise UserError(self.env._("Enter the available quantity of the scarce resource."))
        by_throughput = self.mix_ranking == "throughput"
        rows = []
        for line in self.mix_line_ids.sorted(lambda ln: (ln.sequence, ln.id)):
            mc = line.price_unit - line.variable_unit
            throughput = line.price_unit - line.material_unit
            rows.append({
                "line": line, "mc_unit": mc, "throughput_unit": throughput,
                "mc_per_resource": mc / line.resource_per_unit,
                "throughput_per_resource": throughput / line.resource_per_unit,
            })
        key = "throughput_per_resource" if by_throughput else "mc_per_resource"
        remaining = self.resource_available
        vals_list = []
        for rank, row in enumerate(sorted(rows, key=lambda r: -r[key]), start=1):  # stable: ties keep line order
            line = row["line"]
            planned = min(line.max_demand, remaining / line.resource_per_unit) if row[key] > 0 else 0.0
            used = planned * line.resource_per_unit
            remaining -= used
            vals_list.append({
                "decision_id": self.id, "mix_line_id": line.id, "rank": rank, "mc_unit": row["mc_unit"],
                "mc_per_resource": row["mc_per_resource"], "throughput_per_resource": row["throughput_per_resource"],
                "planned_qty": planned, "resource_used": used, "contribution": planned * row["mc_unit"],
                "throughput": planned * row["throughput_unit"],
            })
        # why sudo: users read the results, the engine writes them (one create for the whole mix)
        self.env["cmc.decision.mix.result"].sudo().create(vals_list)
        total_mc = sum(vals["contribution"] for vals in vals_list)
        self.write({
            "mix_total_mc": total_mc, "mix_total_throughput": sum(vals["throughput"] for vals in vals_list),
            "mix_result": total_mc - self.fixed_cost, "resource_used": sum(vals["resource_used"] for vals in vals_list),
        })

    def _cmc_effective_rate(self):
        """Discount rate (percent) consistent with the basis of the flows (Fisher, exact)."""
        rate, inflation = self.discount_rate / 100, self.inflation_rate / 100
        if self.rate_basis == self.flows_basis:
            return self.discount_rate
        if self.flows_basis == "real":
            return cmc_utils.real_rate(rate, inflation) * 100
        return cmc_utils.nominal_rate(rate, inflation) * 100

    def _cmc_compute_investment(self):
        if not self.cash_flow_ids:
            raise UserError(self.env._("Enter the cash flows of the investment."))
        by_year = defaultdict(lambda: self.env["cmc.decision.cash.flow"])
        for flow in self.cash_flow_ids:
            by_year[flow.year] |= flow
        taxes, nets, amounts = [], [], []
        for year in range(max(by_year) + 1):
            flows = by_year[year]
            amount = sum(flows.mapped("amount"))
            # RN-002: capex / working capital / salvage (non-taxable lines) stay out of the tax base
            base = sum(flows.filtered("taxable").mapped("amount")) - sum(flows.mapped("depreciation"))
            tax = self.tax_rate / 100 * base if year else 0.0  # year 0 is untaxed
            amounts.append(amount)
            taxes.append(tax)
            nets.append(amount - tax)
        rate = self._cmc_effective_rate()
        factors = [1 / (1 + rate / 100) ** year for year in range(len(nets))]
        present = [net * factor for net, factor in zip(nets, factors, strict=True)]
        vals_list, cumulative, cumulative_pv = [], 0.0, 0.0
        for year, (net, tax, factor, pv) in enumerate(zip(nets, taxes, factors, present, strict=True)):
            cumulative += net
            cumulative_pv += pv
            vals_list.append({
                "decision_id": self.id, "year": year, "amount": amounts[year], "tax": tax, "net_flow": net,
                "discount_factor": factor, "present_value": pv, "cumulative_flow": cumulative, "cumulative_pv": cumulative_pv,
            })
        # why sudo: users read the table, the engine writes it
        self.env["cmc.decision.cash.flow.result"].sudo().create(vals_list)
        irr, note = None, False
        try:
            irr = cmc_utils.irr(nets) * 100
        except cmc_utils.MultipleIrrError:
            note = self.env._("Multiple IRR: the flows change sign more than once, no single rate is stated.")
        except ValueError:
            pass  # no sign change: no IRR, nothing to explain
        payback = cmc_utils.payback(nets)
        discounted = cmc_utils.discounted_payback(rate / 100, nets)
        invested = -nets[0]
        outflow = -sum(pv for pv in present if pv < 0)
        self._cmc_finish(
            0.0, sum(present), effective_rate=rate, npv=sum(present), has_irr=irr is not None, irr=irr or 0.0, irr_note=note,
            has_payback=payback is not None, payback_years=payback or 0.0,
            has_discounted_payback=discounted is not None, discounted_payback_years=discounted or 0.0,
            roi=(sum(nets[1:]) - invested) / invested * 100 if invested > 0 else 0.0,
            cbr=sum(pv for pv in present if pv > 0) / outflow if outflow else 0.0,
        )


class CmcDecisionLine(models.Model):
    _name = "cmc.decision.line"
    _inherit = ["cmc.run.line.mixin"]
    _description = "Decision line (revenue / cost, current vs alternative)"
    _order = "sequence, id"
    _check_company_auto = True
    _cmc_run_field = "decision_id"

    decision_id = fields.Many2one("cmc.decision", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="decision_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="decision_id.currency_id")
    sequence = fields.Integer(default=10)
    name = fields.Char(required=True)
    line_type = fields.Selection([("revenue", "Revenue"), ("cost", "Cost")], required=True, default="cost")
    element_id = fields.Many2one("cmc.cost.element", string="Cost Element", check_company=True)
    cost_behavior = fields.Selection([("variable", "Variable"), ("fixed", "Fixed")], default="fixed", required=True)
    amount_current = fields.Monetary()
    amount_alternative = fields.Monetary()
    avoidable = fields.Boolean(compute="_compute_flags", store=True, readonly=False)
    sunk = fields.Boolean(compute="_compute_flags", store=True, readonly=False)
    relevant = fields.Boolean(compute="_compute_relevant", store=True)

    @api.depends("element_id.cmc_avoidable", "element_id.cmc_sunk")
    def _compute_flags(self):
        for line in self:
            line.avoidable = line.element_id.cmc_avoidable
            line.sunk = line.element_id.cmc_sunk

    def _cmc_is_avoidable(self):
        # RN-001: producing nothing incurs no variable cost, whatever the element flag
        self.ensure_one()
        return self.cost_behavior == "variable" or self.avoidable

    @api.depends("sunk", "amount_current", "amount_alternative", "currency_id")
    def _compute_relevant(self):
        for line in self:
            line.relevant = not line.sunk and bool(line.currency_id.compare_amounts(line.amount_current, line.amount_alternative))


class CmcDecisionActivity(models.Model):
    _name = "cmc.decision.activity"
    _inherit = ["cmc.run.line.mixin"]
    _description = "Decision activity consumption (rate x quantity)"
    _order = "id"
    _check_company_auto = True
    _cmc_run_field = "decision_id"

    decision_id = fields.Many2one("cmc.decision", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="decision_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="decision_id.currency_id")
    name = fields.Char(required=True)
    holder_model = fields.Char(required=True)
    holder_id = fields.Many2oneReference(model_field="holder_model", required=True)
    driver_id = fields.Many2one("cmc.cost.driver", required=True)
    quantity = fields.Float(digits="Product Unit")
    side = fields.Selection([("current", "Current"), ("alternative", "Alternative")], default="alternative", required=True)
    rate_value = fields.Float(readonly=True, copy=False, digits=(16, 6))
    amount = fields.Monetary(readonly=True, copy=False)

    def _cmc_guard_runs(self, runs):
        # why: the snapshot is written by Compute (sentinel, not forgeable over RPC) while the run is still
        # draft; skipping the per-line row lock keeps Compute independent of the number of lines (AD-114)
        if self.env.context.get("cmc_run_state") is not CMC_RUN_STATE:
            super()._cmc_guard_runs(runs)


class CmcDecisionMixLine(models.Model):
    _name = "cmc.decision.mix.line"
    _inherit = ["cmc.run.line.mixin"]
    _description = "Decision mix product"
    _order = "sequence, id"
    _check_company_auto = True
    _cmc_run_field = "decision_id"

    decision_id = fields.Many2one("cmc.decision", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="decision_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="decision_id.currency_id")
    sequence = fields.Integer(default=10)
    name = fields.Char(required=True)
    product_id = fields.Many2one("product.product", check_company=True)
    price_unit = fields.Monetary()
    variable_unit = fields.Monetary()
    material_unit = fields.Monetary(help="Totally variable cost (materials) of the throughput accounting view.")
    resource_per_unit = fields.Float(default=1.0, digits="Product Unit", help="Scarce resource used by one unit.")
    max_demand = fields.Float(digits="Product Unit")

    @api.constrains("resource_per_unit")
    def _check_resource_per_unit(self):
        if self.filtered(lambda line: line.resource_per_unit <= 0):
            raise ValidationError(self.env._("A product must use a positive quantity of the scarce resource."))


class CmcDecisionCashFlow(models.Model):
    _name = "cmc.decision.cash.flow"
    _inherit = ["cmc.run.line.mixin"]
    _description = "Decision cash flow (one per year and kind)"
    _order = "year"
    _cmc_run_field = "decision_id"

    decision_id = fields.Many2one("cmc.decision", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="decision_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="decision_id.currency_id")
    year = fields.Integer(required=True)
    amount = fields.Monetary(help="Net pre-tax flow of the year; the investment of year 0 is negative.")
    depreciation = fields.Monetary()
    taxable = fields.Boolean(default=True, help="Untick capex, working capital and salvage at book value: out of the tax base.")

    _year_positive = models.Constraint("CHECK(year >= 0)", "The year cannot be negative.")
    _year_uniq = models.UniqueIndex("(decision_id, year, taxable)", "One cash flow per year and kind.")


class CmcDecisionMixResult(models.Model):
    _name = "cmc.decision.mix.result"
    _inherit = ["cmc.run.result.mixin"]
    _description = "Decision mix result"
    _order = "decision_id, rank"

    decision_id = fields.Many2one("cmc.decision", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="decision_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="decision_id.currency_id")
    mix_line_id = fields.Many2one("cmc.decision.mix.line", ondelete="cascade")
    rank = fields.Integer()
    mc_unit = fields.Monetary()
    mc_per_resource = fields.Float(digits=(16, 4))
    throughput_per_resource = fields.Float(digits=(16, 4))
    planned_qty = fields.Float(digits="Product Unit")
    resource_used = fields.Float(digits="Product Unit")
    contribution = fields.Monetary()
    throughput = fields.Monetary()


class CmcDecisionCashFlowResult(models.Model):
    _name = "cmc.decision.cash.flow.result"
    _inherit = ["cmc.run.result.mixin"]
    _description = "Decision cash flow result"
    _order = "decision_id, year"

    decision_id = fields.Many2one("cmc.decision", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="decision_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="decision_id.currency_id")
    year = fields.Integer()
    amount = fields.Monetary()
    tax = fields.Monetary()
    net_flow = fields.Monetary()
    discount_factor = fields.Float(digits=(16, 8))
    present_value = fields.Monetary()
    cumulative_flow = fields.Monetary()
    cumulative_pv = fields.Monetary()
