from odoo import api, fields, models
from odoo.tools import float_is_zero


class CmcCvrAnalysis(models.Model):
    _name = "cmc.cvr.analysis"
    _description = "Cost-Volume-Profit / Break-even Analysis"
    _order = "date_to desc, name"
    _check_company_auto = True

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
    fixed_cost = fields.Monetary(
        string="Fixed Cost (CF)", compute="_compute_fixed_cost",
        store=True, readonly=False,
    )
    target_profit = fields.Monetary(string="Target Profit (Uo)")
    volume = fields.Float(string="Volume (Q)", digits="Product Unit of Measure")

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
        digits="Product Unit of Measure",
    )
    bep_revenue = fields.Monetary("Break-even Revenue", compute="_compute_cvr", store=True)
    target_qty = fields.Float(
        "Target-Profit Qty (Qo)", compute="_compute_cvr", store=True,
        digits="Product Unit of Measure",
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

    @api.depends("center_id", "date_from", "date_to", "mode")
    def _compute_fixed_cost(self):
        for rec in self:
            if not (rec.mode == "consuntivo" and rec.center_id
                    and rec.date_from and rec.date_to):
                continue  # keep the manual / simulation value
            groups = rec.center_id._cmc_cost_groups(
                rec.date_from, rec.date_to, behavior="fixed", company=rec.company_id
            )
            rec.fixed_cost = groups.get(rec.center_id.id, 0.0)

    @api.depends(
        "price_unit", "variable_unit", "fixed_cost", "target_profit", "volume",
        "is_multiproduct",
        "line_ids.qty", "line_ids.price_unit", "line_ids.variable_unit",
    )
    def _compute_cvr(self):
        for rec in self:
            cf = rec.fixed_cost
            if rec.is_multiproduct and rec.line_ids:
                tot_rev = sum(l.qty * l.price_unit for l in rec.line_ids)
                tot_mc = sum(
                    l.qty * (l.price_unit - l.variable_unit) for l in rec.line_ids
                )
                ratio = (tot_mc / tot_rev) if tot_rev else 0.0
                rec.mc_unit = 0.0
                rec.mc_ratio = ratio
                rec.has_breakeven = ratio > 0
                rec.bep_qty = 0.0
                rec.bep_revenue = (cf / ratio) if ratio else 0.0
                rec.target_qty = 0.0
                mc_total = tot_mc
                actual_rev = tot_rev
            else:
                p, v = rec.price_unit, rec.variable_unit
                mc = p - v
                ratio = (mc / p) if p else 0.0
                rec.mc_unit = mc
                rec.mc_ratio = ratio
                rec.has_breakeven = mc > 0
                if mc > 0:
                    rec.bep_qty = cf / mc
                    rec.bep_revenue = (cf / ratio) if ratio else 0.0
                    rec.target_qty = (rec.target_profit + cf) / mc
                else:
                    rec.bep_qty = rec.bep_revenue = rec.target_qty = 0.0
                mc_total = mc * rec.volume
                actual_rev = p * rec.volume
            rounding = rec.company_id.currency_id.rounding or 0.01
            # margin of safety
            rec.margin_of_safety = actual_rev - rec.bep_revenue
            rec.mos_ratio = (
                rec.margin_of_safety / actual_rev * 100
                if not float_is_zero(actual_rev, precision_rounding=rounding)
                else 0.0
            )
            # operating leverage LO = MC_total / UO (guard near-zero UO, not just 0)
            uo = mc_total - cf
            rec.operating_leverage = (
                (mc_total / uo)
                if not float_is_zero(uo, precision_rounding=rounding)
                else 0.0
            )

    def leverage_impact(self, revenue_change_pct):
        """Estimate the % change of operating profit for a % change of revenue."""
        self.ensure_one()
        return self.operating_leverage * revenue_change_pct

    def action_recompute(self):
        for rec in self:
            # re-derive the fixed cost from current analytic lines (consuntivo),
            # then rebuild the break-even curve — never from a stale CF
            rec.invalidate_recordset(["fixed_cost"])
            rec._compute_fixed_cost()
            rec._build_curve()
        return True

    def _build_curve(self):
        self.ensure_one()
        self.curve_ids.unlink()
        if not self.has_breakeven or self.is_multiproduct:
            return
        p, v, cf = self.price_unit, self.variable_unit, self.fixed_cost
        points = [0.0, self.bep_qty, self.bep_qty * 1.5, self.bep_qty * 2]
        self.env["cmc.cvr.curve"].create(
            [
                {
                    "analysis_id": self.id,
                    "volume": q,
                    "revenue": p * q,
                    "total_cost": cf + v * q,
                }
                for q in points
            ]
        )


class CmcCvrLine(models.Model):
    _name = "cmc.cvr.line"
    _description = "CVR Product Line (mix)"

    analysis_id = fields.Many2one(
        "cmc.cvr.analysis", required=True, ondelete="cascade", index=True
    )
    company_id = fields.Many2one(related="analysis_id.company_id", store=True)
    currency_id = fields.Many2one(related="analysis_id.currency_id")
    product_id = fields.Many2one("product.product")
    qty = fields.Float(digits="Product Unit of Measure")
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
    currency_id = fields.Many2one(related="analysis_id.currency_id")
    volume = fields.Float(digits="Product Unit of Measure")
    revenue = fields.Monetary()
    total_cost = fields.Monetary()
