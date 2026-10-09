from collections import defaultdict

from markupsafe import Markup

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare, float_is_zero, float_round

from odoo.addons.cmc_base.models import cmc_utils
from odoo.addons.cmc_base.models.cmc_utils import qty_rounding

# ratio fields carry 4 decimals (see mc_ratio digits): compare them at that step,
# never at the currency step
RATIO_ROUNDING = 0.0001

# why a sentinel: the context travels over RPC, so a plain True could be forged by a
# client; an ``object()`` cannot be serialised (same as CMC_AUTO in cmc_base).
_SKIP_CURVE = object()

LEVERS = [("price", "Price"), ("volume", "Volume"), ("variable_unit", "Unit variable cost"), ("fixed_cost", "Fixed cost")]


class CmcCvrAnalysis(models.Model):
    _name = "cmc.cvr.analysis"
    _description = "Cost-Volume-Profit / Break-even Analysis"
    _order = "date_to desc, name"
    _check_company_auto = True

    # a write on any of these invalidates the plotted break-even curve
    CURVE_TRIGGERS = frozenset(
        {"price_unit", "variable_unit", "fixed_cost", "is_multiproduct",
         "mode", "center_id", "date_from", "date_to"}
    )
    # a write on any of these changes the source of the actual-mode fixed cost
    FIXED_COST_TRIGGERS = frozenset({"mode", "center_id", "date_from", "date_to"})

    name = fields.Char(required=True, default=lambda s: s.env._("CVR Analysis"))
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda s: s.env.company, index=True
    )
    currency_id = fields.Many2one(related="company_id.currency_id")
    center_id = fields.Many2one(
        "account.analytic.account", string="Cost Center", index=True,
        check_company=True, domain="[('cmc_center_type', '!=', False)]",
    )
    date_from = fields.Date(required=True, default=fields.Date.context_today)
    date_to = fields.Date(required=True, default=fields.Date.context_today)
    mode = fields.Selection(
        [("consuntivo", "Actual (derived)"), ("simulazione", "Simulation (override)")],
        default="consuntivo", required=True,
    )
    is_multiproduct = fields.Boolean()

    # inputs (manual; fixed cost derivable from the center)
    price_unit = fields.Monetary(string="Unit Price (p)")
    variable_unit = fields.Monetary(string="Unit Variable Cost (v)")
    # plain stored value: an editable compute that skips branches is nulled by
    # the ORM for the records it leaves unset (a CF typed in simulation mode was
    # silently reset), and no @api.depends can track the analytic lines behind
    # the actual mode. It is derived on change and by action_recompute() instead.
    fixed_cost = fields.Monetary(string="Fixed Cost (CF)")
    target_profit = fields.Monetary(string="Target Profit (Uo)")
    volume = fields.Float(string="Volume (Q)", digits="Product Unit")
    target_basis = fields.Selection(
        [("pretax", "Pre-tax profit"), ("after_tax", "After-tax profit"), ("margin_pct", "% of sales")],
        default="pretax", required=True,
    )
    tax_rate = fields.Float(string="Tax Rate %", digits=(16, 2))
    target_margin_pct = fields.Float(string="Target Margin %", digits=(16, 2))
    range_warning = fields.Text(readonly=True, copy=False)
    # what-if scratch inputs of the Sensitivity page
    sens_pct = fields.Float(string="Change %", default=10.0, digits=(16, 2))
    iso_lever = fields.Selection(LEVERS, string="If this changes", default="fixed_cost")
    iso_pct = fields.Float(string="By %", default=10.0, digits=(16, 2))
    iso_compensate = fields.Selection(LEVERS, string="Compensate with", default="price")
    alt_variable_unit = fields.Monetary(string="Alternative Unit Variable Cost")
    alt_fixed_cost = fields.Monetary(string="Alternative Fixed Cost")
    sensitivity_html = fields.Html(compute="_compute_whatif", sanitize=False)
    iso_profit_html = fields.Html(compute="_compute_whatif", sanitize=False)
    structure_html = fields.Html(compute="_compute_whatif", sanitize=False)

    line_ids = fields.One2many("cmc.cvr.line", "analysis_id")
    curve_ids = fields.One2many("cmc.cvr.curve", "analysis_id")

    # derived outputs
    mc_unit = fields.Monetary("Unit MC (p-v)", compute="_compute_cvr", store=True)
    mc_ratio = fields.Float(
        "MC Ratio", compute="_compute_cvr", store=True, digits=(16, 4)
    )
    has_breakeven = fields.Boolean(
        "Break-even Exists", compute="_compute_cvr", store=True
    )
    bep_qty = fields.Float(
        "Break-even Qty (Q*)", compute="_compute_cvr", store=True,
        digits="Product Unit",
    )
    bep_revenue = fields.Monetary("Break-even Revenue", compute="_compute_cvr", store=True)
    target_qty = fields.Float(
        "Target-Profit Qty (Qo)", compute="_compute_cvr", store=True,
        digits="Product Unit",
    )
    target_profit_pretax = fields.Monetary("Pre-tax Target Profit", compute="_compute_cvr", store=True)
    target_revenue = fields.Monetary("Target Revenue", compute="_compute_cvr", store=True)
    margin_of_safety = fields.Monetary(
        "Margin of Safety", compute="_compute_cvr", store=True
    )
    mos_ratio = fields.Float(
        "Margin of Safety %", compute="_compute_cvr", store=True, digits=(16, 2)
    )
    operating_leverage = fields.Float(
        "Operating Leverage (LO)", compute="_compute_cvr", store=True, digits=(16, 2)
    )

    _dates_ok = models.Constraint(
        "CHECK(date_to >= date_from)", "End date must be on/after start date."
    )
    _price_non_negative = models.Constraint(
        "CHECK(price_unit >= 0 AND variable_unit >= 0)",
        "Price and variable cost cannot be negative.",
    )

    @api.constrains("tax_rate")
    def _check_tax_rate(self):
        if self.filtered(lambda rec: not 0 <= rec.tax_rate < 100):
            raise ValidationError(self.env._("The tax rate must be at least 0 and below 100%."))

    # ---- fixed cost (actual mode) ---------------------------------------

    def _derive_fixed_cost(self):
        """Pull the fixed cost of the center from its analytic lines. Only in
        actual mode: a simulation CF is typed by the user and kept as is.
        One cost query per (company, period), whatever the number of analyses."""
        derived = self.filtered(
            lambda rec: rec.mode == "consuntivo" and rec.center_id and rec.date_from and rec.date_to
        )
        batches = defaultdict(lambda: self.browse())
        for rec in derived:
            batches[(rec.company_id, rec.date_from, rec.date_to)] |= rec
        for (company, date_from, date_to), recs in batches.items():
            groups = recs.center_id._cmc_cost_groups(date_from, date_to, behavior="fixed", company=company)
            for rec in recs:
                # why: the callers rebuild the curve right after; the nested write
                # (fixed_cost is a curve trigger) would build it a second time
                rec.with_context(cmc_skip_curve=_SKIP_CURVE).fixed_cost = groups.get(rec.center_id.id, 0.0)

    def _refresh_range_warning(self):
        """G-1: the break-even is a linear statement; say so when the volume or the break-even
        quantity leave the relevant range of a cost function of the center. One read for all."""
        centers = self.center_id
        functions = defaultdict(lambda: self.env["cmc.cost.function"])
        for function in self.env["cmc.cost.function"].search([("center_id", "in", centers.ids)]) if centers else ():
            functions[(function.center_id.id, function.company_id.id)] |= function
        for rec in self:
            warnings = [
                warning
                for function in functions[(rec.center_id.id, rec.company_id.id)]
                for qty in (rec.volume, rec.bep_qty) if qty
                for warning in [function._cmc_range_warning(qty)] if warning
            ] if rec.center_id else []
            rec.range_warning = "\n".join(warnings) or False

    @api.onchange("mode", "center_id", "date_from", "date_to")
    def _onchange_fixed_cost_source(self):
        self._derive_fixed_cost()

    # ---- CVP maths -------------------------------------------------------

    @api.depends(
        "price_unit", "variable_unit", "fixed_cost", "target_profit", "volume",
        "is_multiproduct", "target_basis", "tax_rate", "target_margin_pct",
        "line_ids.qty", "line_ids.price_unit", "line_ids.variable_unit",
    )
    def _compute_cvr(self):
        for rec in self:
            mix = [(l.qty, l.price_unit, l.variable_unit) for l in rec.line_ids] if rec.is_multiproduct else []
            rec.update(self._cvp_figures(
                rec.price_unit, rec.variable_unit, rec.fixed_cost, rec.target_profit, rec.volume,
                mix, rec.company_id.currency_id.rounding or 0.01,
                rec.target_basis, rec.tax_rate, rec.target_margin_pct,
            ))

    @api.model
    def _cvp_figures(self, p, v, cf, target_profit, volume, mix, rounding, target_basis="pretax", tax_rate=0.0, target_margin_pct=0.0):
        """Pure CVP maths: {field: value} of the derived outputs. ``mix`` holds
        (qty, price, variable) per product of a multi-product analysis, else is
        empty. A margin below the currency step is rounding noise, not a margin:
        comparing bare floats turned v=9.999 against p=10.00 into a break-even
        quantity three orders of magnitude off."""
        pretax = target_profit / (1 - tax_rate / 100) if target_basis == "after_tax" and tax_rate < 100 else target_profit
        if mix:
            tot_rev = sum(qty * price for qty, price, _var in mix)
            tot_mc = sum(qty * (price - var) for qty, price, var in mix)
            has_rev = not float_is_zero(tot_rev, precision_rounding=rounding)
            ratio = (tot_mc / tot_rev) if has_rev else 0.0
            positive_mc = float_compare(tot_mc, 0.0, precision_rounding=rounding) > 0
            tot_qty = sum(qty for qty, _price, _var in mix)
            res = {
                "mc_unit": 0.0,
                "mc_ratio": ratio,
                "has_breakeven": positive_mc and has_rev,
                # constant mix: Q* = CF / sum(alpha_j x mc_j) with alpha_j = qty_j / sum(qty)
                "bep_qty": cf * tot_qty / tot_mc if positive_mc and tot_qty > 0 else 0.0,
                "bep_revenue": (cf / ratio) if not float_is_zero(ratio, precision_rounding=RATIO_ROUNDING) else 0.0,
                "target_qty": 0.0,
                "target_revenue": 0.0,
            }
            mc_total, actual_rev = tot_mc, tot_rev
        else:
            mc = p - v
            has_price = not float_is_zero(p, precision_rounding=rounding)
            ratio = (mc / p) if has_price else 0.0
            has_breakeven = float_compare(mc, 0.0, precision_rounding=rounding) > 0
            res = {"mc_unit": mc, "mc_ratio": ratio, "has_breakeven": has_breakeven,
                   "bep_qty": 0.0, "bep_revenue": 0.0, "target_qty": 0.0}
            if has_breakeven:
                res["bep_qty"] = cf / mc
                res["bep_revenue"] = (
                    (cf / ratio) if not float_is_zero(ratio, precision_rounding=RATIO_ROUNDING) else 0.0
                )
                res["target_qty"] = (pretax + cf) / mc
            if target_basis == "margin_pct":
                # Frost: profit = PM x sales -> Q = CF / (p - v - PM x p), unreachable when that is <= 0
                margin = mc - target_margin_pct / 100 * p
                reachable = float_compare(margin, 0.0, precision_rounding=rounding) > 0
                res["target_qty"] = cf / margin if reachable else 0.0
                pretax = target_margin_pct / 100 * p * res["target_qty"]
            res["target_revenue"] = float_round(res["target_qty"] * p, precision_rounding=rounding)
            mc_total, actual_rev = mc * volume, p * volume
        # monetary outputs at the currency step, as the stored fields hold
        # them: margin and ratio are derived from the rounded figures
        res["bep_revenue"] = float_round(res["bep_revenue"], precision_rounding=rounding)
        res["target_profit_pretax"] = float_round(pretax, precision_rounding=rounding)
        res["margin_of_safety"] = float_round(actual_rev - res["bep_revenue"], precision_rounding=rounding)
        res["mos_ratio"] = (
            res["margin_of_safety"] / actual_rev * 100
            if not float_is_zero(actual_rev, precision_rounding=rounding)
            else 0.0
        )
        # operating leverage LO = MC_total / UO (guard near-zero UO, not just 0)
        uo = mc_total - cf
        res["operating_leverage"] = (mc_total / uo) if not float_is_zero(uo, precision_rounding=rounding) else 0.0
        return res

    # ---- sensitivity, iso-profit, cost structures (DC-08) -----------------

    def _cmc_profit(self, price=None, volume=None, variable_unit=None, fixed_cost=None):
        self.ensure_one()
        price = self.price_unit if price is None else price
        volume = self.volume if volume is None else volume
        variable_unit = self.variable_unit if variable_unit is None else variable_unit
        fixed_cost = self.fixed_cost if fixed_cost is None else fixed_cost
        return (price - variable_unit) * volume - fixed_cost

    def _cmc_levers(self):
        self.ensure_one()
        return {"price": self.price_unit, "volume": self.volume, "variable_unit": self.variable_unit, "fixed_cost": self.fixed_cost}

    def _cmc_sensitivity(self, pct):
        """Profit when each lever moves by `pct` %, the others unchanged."""
        self.ensure_one()
        base = self._cmc_profit()
        rows = []
        for lever, value in self._cmc_levers().items():
            profit = self._cmc_profit(**{lever: value * (1 + pct / 100)})
            rows.append({
                "lever": lever, "base_profit": base, "profit": profit, "delta": profit - base,
                "delta_pct": (profit - base) / abs(base) * 100 if base else 0.0,
            })
        return rows

    def _cmc_iso_profit(self, lever, pct, compensate):
        """% change of `compensate` keeping the profit unchanged when `lever` moves by `pct` %."""
        self.ensure_one()
        levers = self._cmc_levers()
        if lever not in levers or compensate not in levers or lever == compensate:
            raise UserError(self.env._("Choose two different levers among price, volume, variable cost and fixed cost."))
        base = self._cmc_profit()
        new = dict(levers)
        new[lever] = levers[lever] * (1 + pct / 100)
        price, volume, var, fixed = new["price"], new["volume"], new["variable_unit"], new["fixed_cost"]
        if compensate == "price" and volume:
            target = (base + fixed) / volume + var
        elif compensate == "volume" and price > var:
            target = (base + fixed) / (price - var)
        elif compensate == "variable_unit" and volume:
            target = price - (base + fixed) / volume
        elif compensate == "fixed_cost":
            target = (price - var) * volume - base
        else:
            target = None
        if target is None or not levers[compensate]:
            raise UserError(self.env._("No change of %(lever)s restores the profit.", lever=dict(self._fields["iso_lever"]._description_selection(self.env))[compensate]))
        return (target / levers[compensate] - 1) * 100

    def _cmc_compare_structure(self, variable_unit, fixed_cost):
        """Current structure vs (variable_unit, fixed_cost) at the same price and volume."""
        self.ensure_one()
        current = self._cmc_profit()
        alternative = self._cmc_profit(variable_unit=variable_unit, fixed_cost=fixed_cost)
        return {
            "indifference_qty": cmc_utils.indifference_qty(self.fixed_cost, self.variable_unit, fixed_cost, variable_unit),
            "profit_current": current,
            "profit_alternative": alternative,
            "leverage_current": (self.price_unit - self.variable_unit) * self.volume / current if current else 0.0,
            "leverage_alternative": (self.price_unit - variable_unit) * self.volume / alternative if alternative else 0.0,
        }

    @api.depends(
        "price_unit", "variable_unit", "fixed_cost", "volume", "is_multiproduct", "sens_pct",
        "iso_lever", "iso_pct", "iso_compensate", "alt_variable_unit", "alt_fixed_cost",
    )
    def _compute_whatif(self):
        labels = dict(self._fields["iso_lever"]._description_selection(self.env))
        for rec in self:
            rec.sensitivity_html = rec.iso_profit_html = rec.structure_html = False
            if rec.is_multiproduct:
                continue
            rows = Markup("").join(
                Markup("<tr><td>%s</td><td>%.2f</td><td>%.2f</td><td>%.2f %%</td></tr>")
                % (labels[row["lever"]], row["profit"], row["delta"], row["delta_pct"])
                for row in rec._cmc_sensitivity(rec.sens_pct)
            )
            rec.sensitivity_html = Markup(
                "<table class='table table-sm'><tr><th>%s</th><th>%s</th><th>%s</th><th>%s</th></tr>%s</table>"
            ) % (self.env._("Lever"), self.env._("Profit"), self.env._("Change"), self.env._("Change %"), rows)
            try:
                rec.iso_profit_html = Markup("<p>%s: %.2f %%</p>") % (
                    labels[rec.iso_compensate], rec._cmc_iso_profit(rec.iso_lever, rec.iso_pct, rec.iso_compensate),
                )
            except UserError as err:
                rec.iso_profit_html = Markup("<p class='text-danger'>%s</p>") % err.args[0]
            cmp = rec._cmc_compare_structure(rec.alt_variable_unit, rec.alt_fixed_cost)
            qty = cmp["indifference_qty"]
            rec.structure_html = Markup("<p>%s: %s<br/>%s: %.2f / %.2f<br/>%s: %.2f / %.2f</p>") % (
                self.env._("Indifference quantity"), "%.2f" % qty if qty is not None else "-",
                self.env._("Profit (current / alternative)"), cmp["profit_current"], cmp["profit_alternative"],
                self.env._("Operating leverage (current / alternative)"), cmp["leverage_current"], cmp["leverage_alternative"],
            )

    def action_recompute(self):
        # re-derive the fixed cost from current analytic lines (consuntivo),
        # then rebuild the break-even curve — never from a stale CF
        self._derive_fixed_cost()
        self._refresh_range_warning()
        self._build_curve()
        return True

    # ---- break-even curve ------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        # explicit fixed_cost wins; create preserves vals order. A fixed_cost
        # coming only from context (default_fixed_cost) is not in vals_list and is
        # overridden by the derivation in actual mode, as intended.
        records.browse(
            [r.id for r, v in zip(records, vals_list) if "fixed_cost" not in v]
        )._derive_fixed_cost()
        records._refresh_range_warning()
        records._build_curve()
        return records

    def write(self, vals):
        res = super().write(vals)
        if "fixed_cost" not in vals and self.FIXED_COST_TRIGGERS & vals.keys():
            self._derive_fixed_cost()  # before the curve, which reads fixed_cost
            self._refresh_range_warning()
        # the curve is a snapshot of the parameters: never leave the tab showing
        # points computed from the previous ones
        if self.CURVE_TRIGGERS & vals.keys() and self.env.context.get("cmc_skip_curve") is not _SKIP_CURVE:
            self._build_curve()
        return res

    def _build_curve(self):
        """(Re)build the plotted points. No break-even (or a zero one, whose
        four samples would collapse on the origin) means no curve at all."""
        self.curve_ids.unlink()
        qty_step = qty_rounding(self.env)
        vals_list = []
        for rec in self:
            if rec.is_multiproduct or not rec.has_breakeven:
                continue
            if float_is_zero(rec.bep_qty, precision_rounding=qty_step):
                continue
            p, v, cf = rec.price_unit, rec.variable_unit, rec.fixed_cost
            # why: bep_qty is stored rounded to the quantity digits, so revenue and
            # cost computed on it differ by a few cents at the break-even point
            bep = cf / (p - v)
            vals_list += [
                {
                    "analysis_id": rec.id,
                    "volume": q,
                    "revenue": p * q,
                    "total_cost": cf + v * q,
                }
                for q in (0.0, bep, bep * 1.5, bep * 2)
            ]
        if vals_list:
            self.env["cmc.cvr.curve"].create(vals_list)


class CmcCvrLine(models.Model):
    _name = "cmc.cvr.line"
    _description = "CVR Product Line (mix)"

    analysis_id = fields.Many2one(
        "cmc.cvr.analysis", required=True, ondelete="cascade", index=True
    )
    company_id = fields.Many2one(related="analysis_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="analysis_id.currency_id")
    product_id = fields.Many2one("product.product")
    qty = fields.Float(digits="Product Unit")
    price_unit = fields.Monetary()
    variable_unit = fields.Monetary()
    mc_unit = fields.Monetary(compute="_compute_mc", store=True)
    mix_share = fields.Float(compute="_compute_bep_share", store=True, digits=(16, 4))
    bep_qty = fields.Float(compute="_compute_bep_share", store=True, digits="Product Unit")
    bep_revenue = fields.Monetary(compute="_compute_bep_share", store=True)

    @api.depends("price_unit", "variable_unit")
    def _compute_mc(self):
        for line in self:
            line.mc_unit = line.price_unit - line.variable_unit

    @api.depends("qty", "price_unit", "analysis_id.bep_qty", "analysis_id.is_multiproduct", "analysis_id.line_ids.qty")
    def _compute_bep_share(self):
        for line in self:
            total = sum(line.analysis_id.line_ids.mapped("qty")) if line.analysis_id.is_multiproduct else 0.0
            share = line.qty / total if total else 0.0  # unrounded: the field holds 4 digits only
            line.mix_share = share
            line.bep_qty = share * line.analysis_id.bep_qty
            line.bep_revenue = line.bep_qty * line.price_unit


class CmcCvrCurve(models.Model):
    _name = "cmc.cvr.curve"
    _description = "CVR Break-even Curve Point"
    _order = "volume"

    analysis_id = fields.Many2one(
        "cmc.cvr.analysis", required=True, ondelete="cascade", index=True
    )
    company_id = fields.Many2one(
        related="analysis_id.company_id", store=True, index=True
    )
    currency_id = fields.Many2one(related="analysis_id.currency_id")
    volume = fields.Float(digits="Product Unit")
    revenue = fields.Monetary()
    total_cost = fields.Monetary()
