from odoo import api, fields, models
from odoo.exceptions import ValidationError
from odoo.fields import Command

OUTPUT_FIELDS = (
    "revenue_total", "gl_revenue", "variable_cost_total", "contribution_total", "product_sustaining_total",
    "cost_to_serve_total", "channel_cost_total", "business_sustaining_cost", "operating_profit", "capital_charge_total",
    "economic_profit", "ledger_cost", "assigned_cost", "unassigned_cost", "whale_total", "whale_peak", "whale_unrealized",
    "quadrant_margin_threshold", "quadrant_cost_threshold",
)


class CmcProfitRun(models.Model):
    _name = "cmc.profit.run"
    _inherit = ["cmc.run.mixin"]
    _description = "Profitability Run"
    _order = "date_to desc, id desc"
    _check_company_auto = True
    _check_company_domain = models.check_company_domain_parent_of

    name = fields.Char(required=True, default=lambda self: self.env._("Profitability"))
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    date_from = fields.Date(required=True)
    date_to = fields.Date(required=True)
    abc_run_id = fields.Many2one("cmc.abc.run", string="ABC Run", check_company=True, domain="[('state', '=', 'done')]")
    cost_version_id = fields.Many2one(
        "cmc.plan.version", string="Unit Cost Version", check_company=True, domain="[('state', '=', 'done')]",
        help="Approved version whose unit costs price the products; empty = the most recent actual rates.",
    )
    unit_cost_driver_id = fields.Many2one("cmc.cost.driver", string="Unit Cost Driver", check_company=True)
    capital_rate = fields.Float(string="Capital Rate %", help="Yearly cost of the capital tied up in receivables.")
    fin_analysis_id = fields.Many2one(
        "cmc.fin.analysis", string="Financial Analysis", check_company=True, domain="[('state', '=', 'done')]",
        help="Source of COIN and PFN of the SBU margin statement.",
    )
    sbu_line_ids = fields.One2many("cmc.profit.sbu.input", "run_id", string="SBU Capital", copy=True)

    # ---- outputs
    revenue_total = fields.Monetary(readonly=True, copy=False)
    gl_revenue = fields.Monetary(readonly=True, copy=False, help="Revenue the general ledger had when the run was computed.")
    variable_cost_total = fields.Monetary(readonly=True, copy=False)
    contribution_total = fields.Monetary(readonly=True, copy=False)
    product_sustaining_total = fields.Monetary(readonly=True, copy=False)
    cost_to_serve_total = fields.Monetary(readonly=True, copy=False)
    channel_cost_total = fields.Monetary(readonly=True, copy=False)
    business_sustaining_cost = fields.Monetary(readonly=True, copy=False)
    operating_profit = fields.Monetary(readonly=True, copy=False)
    capital_charge_total = fields.Monetary(readonly=True, copy=False)
    economic_profit = fields.Monetary(readonly=True, copy=False)
    ledger_cost = fields.Monetary(readonly=True, copy=False, help="Analytic cost base of the span.")
    assigned_cost = fields.Monetary(readonly=True, copy=False)
    unassigned_cost = fields.Monetary(readonly=True, copy=False, help="Reported, never charged to a customer or a product.")
    whale_total = fields.Monetary(readonly=True, copy=False)
    whale_peak = fields.Monetary(readonly=True, copy=False)
    whale_unrealized = fields.Monetary(readonly=True, copy=False)
    quadrant_margin_threshold = fields.Float(readonly=True, copy=False, digits=(16, 2))
    quadrant_cost_threshold = fields.Float(readonly=True, copy=False, digits=(16, 2))
    missing_cost_product_ids = fields.Many2many(
        "product.product", "cmc_profit_run_missing_product_rel", "run_id", "product_id", readonly=True, copy=False,
        string="Products Without Unit Cost",
    )
    fact_ids = fields.One2many("cmc.profit.fact", "run_id")
    product_ids = fields.One2many("cmc.profit.product", "run_id")
    customer_ids = fields.One2many("cmc.profit.customer", "run_id")
    channel_ids = fields.One2many("cmc.profit.channel", "run_id")
    waterfall_ids = fields.One2many("cmc.profit.waterfall", "run_id")
    supplier_ids = fields.One2many("cmc.profit.supplier", "run_id")
    sbu_ids = fields.One2many("cmc.profit.sbu", "run_id")

    _cmc_input_fields = (
        "company_id", "date_from", "date_to", "abc_run_id", "cost_version_id", "unit_cost_driver_id", "capital_rate",
        "sbu_line_ids", "fin_analysis_id",
    )
    _cmc_result_fields = ("fact_ids", "product_ids", "customer_ids", "channel_ids", "waterfall_ids", "supplier_ids", "sbu_ids")

    @api.constrains("date_from", "date_to")
    def _check_dates(self):
        if any(run.date_to < run.date_from for run in self):
            raise ValidationError(self.env._("The end date cannot precede the start date."))

    def _cmc_reset_values(self):
        return {
            **super()._cmc_reset_values(),
            **dict.fromkeys(OUTPUT_FIELDS, 0.0),
            "missing_cost_product_ids": [Command.clear()],
        }


class CmcProfitSbuInput(models.Model):
    _name = "cmc.profit.sbu.input"
    _inherit = ["cmc.run.line.mixin"]
    _description = "Profitability Run SBU Capital"
    _order = "id"

    run_id = fields.Many2one("cmc.profit.run", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="run_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="run_id.currency_id")
    sbu_value_id = fields.Many2one(
        "cmc.attribute.value", string="SBU", required=True, domain="[('attribute_id.code', '=', 'SBU')]",
    )
    coin_open = fields.Monetary(string="COIN opening")
    coin_close = fields.Monetary(string="COIN closing")
    pfn = fields.Monetary(string="PFN")
    capital_key = fields.Float(string="Capital Key %", help="Share of the financial analysis capital that the SBU holds.")
