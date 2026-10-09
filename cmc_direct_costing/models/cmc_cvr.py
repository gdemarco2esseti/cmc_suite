from collections import defaultdict

from odoo import api, fields, models
from odoo.tools import float_compare, float_is_zero

from odoo.addons.cmc_base.models.cmc_utils import qty_rounding

# ratio fields carry 4 decimals (see mc_ratio digits): compare them at that step,
# never at the currency step
RATIO_ROUNDING = 0.0001

# why a sentinel: the context travels over RPC, so a plain True could be forged by a
# client; an ``object()`` cannot be serialised (same as CMC_AUTO in cmc_base).
_SKIP_CURVE = object()


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

    @api.onchange("mode", "center_id", "date_from", "date_to")
    def _onchange_fixed_cost_source(self):
        self._derive_fixed_cost()

    # ---- CVP maths -------------------------------------------------------

    @api.depends(
        "price_unit", "variable_unit", "fixed_cost", "target_profit", "volume",
        "is_multiproduct",
        "line_ids.qty", "line_ids.price_unit", "line_ids.variable_unit",
    )
    def _compute_cvr(self):
        for rec in self:
            mix = [(l.qty, l.price_unit, l.variable_unit) for l in rec.line_ids] if rec.is_multiproduct else []
            rec.update(self._cvp_figures(
                rec.price_unit, rec.variable_unit, rec.fixed_cost, rec.target_profit, rec.volume,
                mix, rec.company_id.currency_id.rounding or 0.01,
            ))

    @api.model
    def _cvp_figures(self, p, v, cf, target_profit, volume, mix, rounding):
        """Pure CVP maths: {field: value} of the derived outputs. ``mix`` holds
        (qty, price, variable) per product of a multi-product analysis, else is
        empty. A margin below the currency step is rounding noise, not a margin:
        comparing bare floats turned v=9.999 against p=10.00 into a break-even
        quantity three orders of magnitude off."""
        if mix:
            tot_rev = sum(qty * price for qty, price, _var in mix)
            tot_mc = sum(qty * (price - var) for qty, price, var in mix)
            has_rev = not float_is_zero(tot_rev, precision_rounding=rounding)
            ratio = (tot_mc / tot_rev) if has_rev else 0.0
            positive_mc = float_compare(tot_mc, 0.0, precision_rounding=rounding) > 0
            res = {
                "mc_unit": 0.0,
                "mc_ratio": ratio,
                "has_breakeven": positive_mc and has_rev,
                "bep_qty": 0.0,
                "bep_revenue": (cf / ratio) if not float_is_zero(ratio, precision_rounding=RATIO_ROUNDING) else 0.0,
                "target_qty": 0.0,
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
                res["target_qty"] = (target_profit + cf) / mc
            mc_total, actual_rev = mc * volume, p * volume
        res["margin_of_safety"] = actual_rev - res["bep_revenue"]
        res["mos_ratio"] = (
            res["margin_of_safety"] / actual_rev * 100
            if not float_is_zero(actual_rev, precision_rounding=rounding)
            else 0.0
        )
        # operating leverage LO = MC_total / UO (guard near-zero UO, not just 0)
        uo = mc_total - cf
        res["operating_leverage"] = (mc_total / uo) if not float_is_zero(uo, precision_rounding=rounding) else 0.0
        return res

    def action_recompute(self):
        # re-derive the fixed cost from current analytic lines (consuntivo),
        # then rebuild the break-even curve — never from a stale CF
        self._derive_fixed_cost()
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
        records._build_curve()
        return records

    def write(self, vals):
        res = super().write(vals)
        if "fixed_cost" not in vals and self.FIXED_COST_TRIGGERS & vals.keys():
            self._derive_fixed_cost()  # before the curve, which reads fixed_cost
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

    @api.depends("price_unit", "variable_unit")
    def _compute_mc(self):
        for line in self:
            line.mc_unit = line.price_unit - line.variable_unit


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
