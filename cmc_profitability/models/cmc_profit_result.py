from odoo import fields, models

from odoo.addons.cmc_base.models.cmc_utils import month_end

FLOOR_STATUS = [("ok", "OK"), ("below_full", "Below full cost"), ("below_variable", "Below variable cost")]


class CmcProfitResult(models.AbstractModel):
    _name = "cmc.profit.result"
    _inherit = ["cmc.run.result.mixin"]
    _description = "Profitability result row"

    run_id = fields.Many2one("cmc.profit.run", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="run_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="run_id.currency_id")


class CmcProfitFact(models.Model):
    _name = "cmc.profit.fact"
    _inherit = ["cmc.profit.result"]
    _description = "Profit Fact (product x customer x salesperson x month)"
    _order = "date, id"

    date = fields.Date(index=True, help="First day of the month.")
    product_id = fields.Many2one("product.product", index=True)
    partner_id = fields.Many2one("res.partner", string="Customer", index=True)
    salesperson_id = fields.Many2one("res.users", index=True)
    segment_value_id = fields.Many2one("cmc.attribute.value", string="Segment")
    channel_value_id = fields.Many2one("cmc.attribute.value", string="Channel")
    sbu_value_id = fields.Many2one("cmc.attribute.value", string="SBU")
    product_object_id = fields.Many2one("cmc.cost.object")
    customer_object_id = fields.Many2one("cmc.cost.object")
    quantity = fields.Float()
    revenue = fields.Monetary()
    list_amount = fields.Monetary()
    discount_amount = fields.Monetary()
    unit_price = fields.Float(digits=(16, 4), aggregator="avg")
    variable_unit_cost = fields.Float(digits=(16, 4), aggregator="avg")
    full_unit_cost = fields.Float(digits=(16, 4), aggregator="avg")
    variable_cost = fields.Monetary()
    contribution_margin = fields.Monetary()
    product_sustaining_cost = fields.Monetary()
    floor_status = fields.Selection(FLOOR_STATUS)

    def action_cmc_open_journal_items(self):
        """Drill-down by domain on the posted income items of the fact; the action runs as the user."""
        self.ensure_one()
        run = self.run_id
        start = max(self.date, run.date_from)
        end = min(month_end(self.date), run.date_to)
        domain = [
            ("parent_state", "=", "posted"),
            ("company_id", "=", run.company_id.id),
            ("date", ">=", start),
            ("date", "<=", end),
            ("account_id.account_type", "in", ("income", "income_other")),
            ("product_id", "=", self.product_id.id or False),
            ("move_id.invoice_user_id", "=", self.salesperson_id.id or False),
        ]
        domain.append(("partner_id.commercial_partner_id", "=", self.partner_id.id) if self.partner_id else ("partner_id", "=", False))
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Journal items"),
            "res_model": "account.move.line",
            "view_mode": "list,form",
            "domain": domain,
        }


class CmcProfitProduct(models.Model):
    _name = "cmc.profit.product"
    _inherit = ["cmc.profit.result"]
    _description = "Product Profitability"
    _order = "rank, id"
    _rec_name = "product_id"

    product_id = fields.Many2one("product.product", index=True)
    cost_object_id = fields.Many2one("cmc.cost.object")
    sbu_value_id = fields.Many2one("cmc.attribute.value", string="SBU")
    quantity = fields.Float()
    revenue = fields.Monetary()
    variable_cost = fields.Monetary()
    contribution_margin = fields.Monetary()
    product_sustaining_cost = fields.Monetary()
    product_margin = fields.Monetary()
    avg_price = fields.Float(digits=(16, 4), aggregator="avg")
    variable_unit_cost = fields.Float(digits=(16, 4), aggregator="avg")
    full_unit_cost = fields.Float(digits=(16, 4), aggregator="avg")
    floor_status = fields.Selection(FLOOR_STATUS)
    rank = fields.Integer(aggregator=False)
    cumulative_profit_pct = fields.Float(digits=(16, 2), aggregator="max")


class CmcProfitCustomer(models.Model):
    _name = "cmc.profit.customer"
    _inherit = ["cmc.profit.result"]
    _description = "Customer Profitability"
    _order = "rank, id"
    _rec_name = "partner_id"

    partner_id = fields.Many2one("res.partner", string="Customer", index=True)
    cost_object_id = fields.Many2one("cmc.cost.object")
    segment_value_id = fields.Many2one("cmc.attribute.value", string="Segment")
    channel_value_id = fields.Many2one("cmc.attribute.value", string="Channel")
    revenue = fields.Monetary()
    variable_cost = fields.Monetary()
    contribution_margin = fields.Monetary(string="M1 Contribution")
    product_sustaining_cost = fields.Monetary()
    product_margin = fields.Monetary(string="M2 Product Margin")
    cost_to_serve = fields.Monetary()
    served_margin = fields.Monetary(string="M3 Served Margin")
    channel_cost = fields.Monetary()
    customer_profit = fields.Monetary(string="M4 Customer Profit")
    receivable_balance = fields.Monetary()
    capital_charge = fields.Monetary()
    economic_profit = fields.Monetary()
    order_count = fields.Integer()
    list_amount = fields.Monetary()
    discount_amount = fields.Monetary()
    off_invoice_amount = fields.Monetary()
    pocket_amount = fields.Monetary()
    rank = fields.Integer(aggregator=False)
    cumulative_profit = fields.Monetary(aggregator="max")
    cumulative_profit_pct = fields.Float(digits=(16, 2), aggregator="max")
    net_margin_pct = fields.Float(digits=(16, 2), aggregator="avg")
    cost_to_serve_pct = fields.Float(digits=(16, 2), aggregator="avg")
    quadrant = fields.Selection([
        ("passive", "Passive (high margin, low cost-to-serve)"),
        ("carriage", "Carriage trade (high margin, high cost-to-serve)"),
        ("bargain", "Bargain basement (low margin, low cost-to-serve)"),
        ("aggressive", "Aggressive (low margin, high cost-to-serve)"),
    ])


class CmcProfitChannel(models.Model):
    _name = "cmc.profit.channel"
    _inherit = ["cmc.profit.result"]
    _description = "Channel Profitability"
    _order = "id"
    _rec_name = "channel_value_id"

    channel_value_id = fields.Many2one("cmc.attribute.value", string="Channel")
    revenue = fields.Monetary()
    served_margin = fields.Monetary()
    channel_cost = fields.Monetary()
    channel_margin = fields.Monetary()


class CmcProfitWaterfall(models.Model):
    _name = "cmc.profit.waterfall"
    _inherit = ["cmc.profit.result"]
    _description = "Price Waterfall Element Amount"
    _order = "partner_id, element_id"
    _rec_name = "element_id"

    partner_id = fields.Many2one("res.partner", string="Customer", index=True)
    element_id = fields.Many2one("cmc.profit.price.element", ondelete="cascade")
    amount = fields.Monetary()


class CmcProfitSupplier(models.Model):
    _name = "cmc.profit.supplier"
    _inherit = ["cmc.profit.result"]
    _description = "Supplier Total Cost of Ownership"
    _order = "rank, id"
    _rec_name = "partner_id"

    partner_id = fields.Many2one("res.partner", string="Supplier", index=True)
    cost_object_id = fields.Many2one("cmc.cost.object")
    purchase_amount = fields.Monetary()
    activity_cost = fields.Monetary()
    tco = fields.Monetary(string="Total Cost of Ownership")
    tco_index = fields.Float(digits=(16, 4), aggregator="avg")
    rank = fields.Integer(aggregator=False)


class CmcProfitSbu(models.Model):
    _name = "cmc.profit.sbu"
    _inherit = ["cmc.profit.result"]
    _description = "SBU Margin and Cash Flow"
    _order = "id"
    _rec_name = "sbu_value_id"

    sbu_value_id = fields.Many2one("cmc.attribute.value", string="SBU")
    revenue = fields.Monetary()
    variable_cost = fields.Monetary()
    product_sustaining_cost = fields.Monetary()
    mslc = fields.Monetary(string="MSLC", help="Semi-gross contribution margin: revenue + variable + product-sustaining cost.")
    coin_open = fields.Monetary(string="COIN opening")
    coin_close = fields.Monetary(string="COIN closing")
    pfn = fields.Monetary(string="PFN")
    equity = fields.Monetary(string="Notional equity", help="Closing COIN less net financial position.")
    roce = fields.Float(digits=(16, 2), string="ROCE %", aggregator="avg")
    growth = fields.Float(digits=(16, 2), string="g %", aggregator="avg")
    cash_flow = fields.Monetary(string="FCN", help="Net cash flow: MSLC less the COIN growth.")
    sustainable = fields.Boolean()
